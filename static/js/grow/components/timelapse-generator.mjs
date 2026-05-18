/**
 * Time-lapse video generator — Phase 4 #8.
 *
 * Mounted in the History tab below the photo-timelapse scrubber. The
 * operator picks a range (matches /history vocab) + framerate, hits
 * Generate; the request POSTs to /api/grow/units/<id>/timelapse and
 * the server queues a render job. The component then polls the job
 * status every 2s; when status flips to "complete", an inline
 * <video> player + download link replace the form.
 *
 * Server-side ffmpeg invocation lives in mlss_monitor/grow/
 * timelapse_jobs.py. POST returns 503 when ffmpeg isn't installed,
 * which we surface as a helpful "install ffmpeg first" message.
 *
 * RBAC: viewer can see + watch existing renders; controller+ can
 * create new ones.
 */

const _RANGE_OPTIONS = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d",  label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "all", label: "All photos" },
];
const _FPS_OPTIONS = [5, 10, 24];
const _DEFAULT_FPS = 10;
const _POLL_INTERVAL_MS = 2000;

// Job-status terminal states. _refreshLatest stops polling on these.
const _TERMINAL = new Set(["complete", "failed"]);


function _canCreate(role) {
  return role === "controller" || role === "admin";
}


/**
 * @param {object} unit  unit object (we only read `id`)
 * @param {object} opts
 *   - ownerDocument
 *   - currentRole  ("viewer" / "controller" / "admin")
 *   - fetchFn      injected fetch (for tests)
 *   - pollIntervalMs (default 2000)
 *   - setIntervalFn / clearIntervalFn (for tests; defaults to global)
 */
export function renderTimelapseGenerator(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const currentRole = opts.currentRole ?? "viewer";
  const fetchFn = opts.fetchFn || (typeof fetch !== "undefined" ? fetch : null);
  const pollMs = opts.pollIntervalMs ?? _POLL_INTERVAL_MS;
  const setIv = opts.setIntervalFn || setInterval;
  const clearIv = opts.clearIntervalFn || clearInterval;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel timelapse-generator";
  wrap.dataset.testid = "timelapse-generator";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🎬 Time-lapse video</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "tlg-body";
  wrap.appendChild(body);

  // Form (controllers + admins). Viewers see only the latest job.
  let formEl = null;
  if (_canCreate(currentRole)) {
    formEl = doc.createElement("div");
    formEl.className = "tlg-form";
    formEl.dataset.testid = "tlg-form";

    const rangeLabel = doc.createElement("label");
    rangeLabel.className = "tlg-field";
    rangeLabel.appendChild(doc.createTextNode("Range"));
    const rangeSel = doc.createElement("select");
    rangeSel.className = "tlg-range";
    rangeSel.dataset.testid = "tlg-range";
    for (const o of _RANGE_OPTIONS) {
      const opt = doc.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      rangeSel.appendChild(opt);
    }
    rangeLabel.appendChild(rangeSel);

    const fpsLabel = doc.createElement("label");
    fpsLabel.className = "tlg-field";
    fpsLabel.appendChild(doc.createTextNode("Frames/sec"));
    const fpsSel = doc.createElement("select");
    fpsSel.className = "tlg-fps";
    fpsSel.dataset.testid = "tlg-fps";
    for (const f of _FPS_OPTIONS) {
      const opt = doc.createElement("option");
      opt.value = String(f);
      opt.textContent = String(f);
      if (f === _DEFAULT_FPS) opt.selected = true;
      fpsSel.appendChild(opt);
    }
    fpsLabel.appendChild(fpsSel);

    const submit = doc.createElement("button");
    submit.className = "px-btn tlg-submit";
    submit.dataset.testid = "tlg-submit";
    submit.textContent = "Generate";

    formEl.appendChild(rangeLabel);
    formEl.appendChild(fpsLabel);
    formEl.appendChild(submit);
    body.appendChild(formEl);

    submit.addEventListener("click", async () => {
      submit.disabled = true;
      try {
        const resp = await fetchFn(`/api/grow/units/${unit.id}/timelapse`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            range: rangeSel.value,
            fps: parseInt(fpsSel.value, 10),
          }),
        });
        if (resp.status === 503) {
          _renderStatus(
            "ffmpeg isn't installed on the server. Run: sudo apt install ffmpeg",
            "err",
          );
          submit.disabled = false;
          return;
        }
        if (!resp.ok) {
          _renderStatus(`Failed to queue render (HTTP ${resp.status}).`, "err");
          submit.disabled = false;
          return;
        }
        const job = await resp.json();
        _renderStatus(`Queued job ${job.id} — rendering…`, "info");
        _startPolling(job.id);
      } catch (_e) {
        _renderStatus("Network error — could not queue render.", "err");
        submit.disabled = false;
      }
    });
  }

  // Status / output region — used both for "queued / running" and the
  // final "complete" video player.
  const statusEl = doc.createElement("div");
  statusEl.className = "tlg-status";
  statusEl.dataset.testid = "tlg-status";
  body.appendChild(statusEl);

  const outputEl = doc.createElement("div");
  outputEl.className = "tlg-output";
  outputEl.dataset.testid = "tlg-output";
  body.appendChild(outputEl);

  function _renderStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = `tlg-status tlg-status-${kind}`;
  }

  function _renderJobUI(job) {
    if (job.status === "complete" && job.video_url) {
      _renderStatus("", "ok");
      outputEl.textContent = "";
      const video = doc.createElement("video");
      video.className = "tlg-video";
      video.dataset.testid = "tlg-video";
      video.src = job.video_url;
      video.controls = true;
      outputEl.appendChild(video);
      const download = doc.createElement("a");
      download.className = "tlg-download";
      download.href = job.video_url;
      download.download = `timelapse-unit${job.unit_id}-job${job.id}.mp4`;
      download.textContent = "Download MP4";
      outputEl.appendChild(download);
      return;
    }
    if (job.status === "failed") {
      _renderStatus(
        `Render failed: ${job.error_message || "unknown error"}.`,
        "err",
      );
      // Re-enable submit so the operator can retry
      const submit = wrap.querySelector(".tlg-submit");
      if (submit) submit.disabled = false;
      return;
    }
    // queued / running
    const label = job.status === "running" ? "Rendering" : "Queued";
    _renderStatus(`${label} (job ${job.id})…`, "info");
  }

  let _pollHandle = null;
  function _startPolling(jobId) {
    if (_pollHandle) clearIv(_pollHandle);
    const tick = async () => {
      try {
        const r = await fetchFn(`/api/grow/timelapse/${jobId}`);
        if (!r.ok) return;
        const job = await r.json();
        _renderJobUI(job);
        if (_TERMINAL.has(job.status)) {
          clearIv(_pollHandle);
          _pollHandle = null;
          if (job.status === "complete") {
            const submit = wrap.querySelector(".tlg-submit");
            if (submit) submit.disabled = false;
          }
        }
      } catch (_e) {
        // Stay polling; transient network errors recover on the next tick.
      }
    };
    // Fire immediately + then on interval for tighter feedback.
    tick();
    _pollHandle = setIv(tick, pollMs);
  }

  // On mount: fetch the most recent job to surface progress / video without
  // making the operator click Generate again.
  (async () => {
    if (!fetchFn) return;
    try {
      const r = await fetchFn(`/api/grow/units/${unit.id}/timelapse`);
      if (!r.ok) return;
      const jobs = await r.json();
      if (!Array.isArray(jobs) || jobs.length === 0) return;
      const latest = jobs[0];
      _renderJobUI(latest);
      if (!_TERMINAL.has(latest.status)) {
        _startPolling(latest.id);
      }
    } catch (_e) {
      // ignore
    }
  })();

  return wrap;
}
