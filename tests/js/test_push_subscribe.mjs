/** Test the navigator.pushManager subscribe flow. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { subscribeForPush } from "../../static/js/notifications/push-subscribe.mjs";


function _setup() {
  // Fake navigator.serviceWorker.ready + pushManager
  const fakeSub = {
    endpoint: "https://push.example/abc",
    getKey: (name) => new Uint8Array([1, 2, 3]),
    toJSON: () => ({
      endpoint: "https://push.example/abc",
      keys: { p256dh: "AQID", auth: "AQID" },
    }),
  };
  // Node 22+ makes globalThis.navigator a read-only getter, so we must
  // override its property descriptor instead of assigning directly.
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      serviceWorker: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: async () => null,
            subscribe: async () => fakeSub,
          },
        }),
      },
    },
  });
  globalThis.atob = (s) => Buffer.from(s, "base64").toString("binary");
  globalThis.btoa = (s) => Buffer.from(s, "binary").toString("base64");
}


test("subscribeForPush: full happy path", async () => {
  _setup();
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/notifications/vapid-key") {
      return { ok: true, json: async () => ({
        public_key: "BJxQk9V1Rk7XqK8r8sHc1Z0sB-fakelongstring",
      })};
    }
    if (url === "/api/notifications/subscriptions" && opts.method === "POST") {
      return { ok: true, json: async () => ({ id: 42, message: "Subscribed" })};
    }
    return { ok: true, json: async () => ({}) };
  };
  const result = await subscribeForPush({
    fetchFn, deviceLabel: "Test device",
  });
  assert.equal(result.id, 42);
  const post = calls.find(c => c.method === "POST");
  assert.ok(post);
  const body = JSON.parse(post.body);
  assert.equal(body.device_label, "Test device");
  assert.ok(body.endpoint);
  assert.ok(body.p256dh);
  assert.ok(body.auth);
});
