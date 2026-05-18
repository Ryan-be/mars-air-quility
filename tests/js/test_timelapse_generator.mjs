/**
 * Time-lapse video generator — Phase 4 #8 frontend.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderTimelapseGenerator } from
  "../../static/js/grow/components/timelapse-generator.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _flushMicro() {
  return new Promise((resolve) => {
    let n = 16;
    const next = () => (--n <= 0 ? resolve() : Promise.resolve().then(next));
    next();
  });
}


function _stubFetch(routes) {
  return async (url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method}:${url}`;
    if (!(key in routes)) {
      return new Response(JSON.stringify({ error: "no stub" }), { status: 404 });
    }
    const handler = routes[key];
    const result = await handler(url, opts);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  };
}


function _job(overrides = {}) {
  return {
    id: 42,
    unit_id: 7,
    requested_by: "alice",
    requested_at: "2026-05-08T12:00:00",
    range: "24h",
    fps: 10,
    status: "queued",
    output_path: null,
    error_message: null,
    started_at: null,
    completed_at: null,
    video_url: null,
    ...overrides,
  };
}


// ─── Form visibility per role ──────────────────────────────────────────


test("form hidden for viewer role", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [],
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "viewer", fetchFn,
  });
  await _flushMicro();
  assert.equal(el.querySelector("[data-testid='tlg-form']"), null);
});


test("form visible for controller", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [],
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
  });
  await _flushMicro();
  assert.ok(el.querySelector("[data-testid='tlg-form']"));
  assert.ok(el.querySelector("[data-testid='tlg-range']"));
  assert.ok(el.querySelector("[data-testid='tlg-fps']"));
});


// ─── POST flow ─────────────────────────────────────────────────────────


test("clicking Generate POSTs range + fps", async () => {
  let postBody = null;
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [],
    "POST:/api/grow/units/7/timelapse": (_url, opts) => {
      postBody = JSON.parse(opts.body);
      return new Response(
        JSON.stringify(_job({ id: 50, status: "queued" })),
        { status: 202 },
      );
    },
    "GET:/api/grow/timelapse/50": () => _job({ id: 50, status: "queued" }),
  });
  const fakeSetIv = () => 1;
  const fakeClearIv = () => {};
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
    setIntervalFn: fakeSetIv, clearIntervalFn: fakeClearIv,
  });
  await _flushMicro();
  el.querySelector("[data-testid='tlg-range']").value = "7d";
  el.querySelector("[data-testid='tlg-fps']").value = "24";
  el.querySelector("[data-testid='tlg-submit']").click();
  await _flushMicro();
  assert.deepEqual(postBody, { range: "7d", fps: 24 });
});


test("503 from POST surfaces 'install ffmpeg' message", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [],
    "POST:/api/grow/units/7/timelapse": () =>
      new Response(JSON.stringify({ error: "ffmpeg_not_installed" }),
                   { status: 503 }),
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  });
  await _flushMicro();
  el.querySelector("[data-testid='tlg-submit']").click();
  await _flushMicro();
  const status = el.querySelector("[data-testid='tlg-status']");
  assert.match(status.textContent, /ffmpeg/i);
  assert.match(status.className, /err/);
});


// ─── Polling + completion ──────────────────────────────────────────────


test("on mount, surfaces existing latest complete job with video player", async () => {
  const completeJob = _job({
    id: 99, status: "complete",
    output_path: "unit_007/99.mp4",
    video_url: "/api/grow/timelapse/99/video",
  });
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [completeJob],
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "viewer", fetchFn,
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  });
  await _flushMicro();
  const video = el.querySelector("[data-testid='tlg-video']");
  assert.ok(video, "complete job should render a <video>");
  assert.equal(video.src.endsWith("/api/grow/timelapse/99/video"), true);
  assert.ok(el.querySelector(".tlg-download"));
});


test("on mount, queued job triggers polling", async () => {
  let pollTickHandler = null;
  const fakeSetIv = (fn) => { pollTickHandler = fn; return 99; };
  const fakeClearIv = () => {};

  let getCount = 0;
  let currentJob = _job({ id: 7, status: "queued" });
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [currentJob],
    "GET:/api/grow/timelapse/7": () => {
      getCount++;
      // Flip to complete after the second status fetch
      if (getCount >= 2) {
        currentJob = _job({
          id: 7, status: "complete",
          output_path: "unit_007/7.mp4",
          video_url: "/api/grow/timelapse/7/video",
        });
      }
      return currentJob;
    },
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
    setIntervalFn: fakeSetIv, clearIntervalFn: fakeClearIv,
  });
  await _flushMicro();
  // First status tick was the immediate-after-mount call inside _startPolling.
  // A poll handler should now be set up.
  assert.ok(pollTickHandler, "poll handler should be installed");
  // Simulate a poll interval firing
  await pollTickHandler();
  await _flushMicro();
  // Job should now be complete and video element rendered
  assert.ok(el.querySelector("[data-testid='tlg-video']"),
            "completed job should render a video");
});


test("failed job surfaces error message", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [
      _job({ id: 1, status: "failed", error_message: "ffmpeg_not_installed" }),
    ],
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  });
  await _flushMicro();
  const status = el.querySelector("[data-testid='tlg-status']");
  assert.match(status.textContent, /failed/i);
  assert.match(status.textContent, /ffmpeg_not_installed/);
});


test("default fps in form is 10", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/timelapse": () => [],
  });
  const el = renderTimelapseGenerator({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
  });
  await _flushMicro();
  assert.equal(el.querySelector("[data-testid='tlg-fps']").value, "10");
});
