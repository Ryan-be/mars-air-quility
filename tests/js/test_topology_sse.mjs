/**
 * Tests for static/js/topology/sse.mjs (Phase 10 Task 10.1).
 *
 * The wrapper opens a single EventSource against /api/stream and routes
 * the four named events the topology page cares about onto user-supplied
 * callbacks. Each callback receives the JSON-parsed `data` payload.
 *
 * Auto-reconnect behaviour: on an `error` event the wrapper closes the
 * current EventSource and re-opens after a backoff (5s base, doubling
 * to a 60s cap), mirroring the pattern in static/js/dashboard.js.
 *
 * Tests inject `EventSourceCtor` to avoid relying on the browser API
 * under Node. Each fake records its addEventListener calls so the test
 * can synthesise an event by invoking the recorded handler directly.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { subscribe } from "../../static/js/topology/sse.mjs";


function _makeFakeEventSource() {
  // Track every constructed instance so reconnect-tests can interrogate
  // both the original + the post-error replacement.
  const instances = [];
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.onerror = null;
      this.onopen = null;
      this.closed = false;
      instances.push(this);
    }
    addEventListener(name, fn) {
      this.listeners[name] = fn;
    }
    close() {
      this.closed = true;
    }
    // Helper used by tests to synthesise an SSE event.
    _dispatch(name, data) {
      const fn = this.listeners[name];
      if (fn) fn({ data: JSON.stringify(data) });
    }
  }
  return { Ctor: FakeEventSource, instances };
}


test("subscribe: opens EventSource against /api/stream", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const sub = subscribe({
    onEffectorState: () => {},
    onSensorUpdate: () => {},
    onHealthUpdate: () => {},
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  assert.equal(instances.length, 1);
  assert.equal(instances[0].url, "/api/stream");
  sub.close();
});


test("subscribe: effector_state_changed routes JSON-parsed payload to onEffectorState", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const seen = [];
  const sub = subscribe({
    onEffectorState: (d) => seen.push(d),
    onSensorUpdate: () => {},
    onHealthUpdate: () => {},
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  instances[0]._dispatch("effector_state_changed", {
    id: 1, state: "on", auto: false,
  });
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0], { id: 1, state: "on", auto: false });
  sub.close();
});


test("subscribe: sensor_update routes to onSensorUpdate", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const seen = [];
  const sub = subscribe({
    onEffectorState: () => {},
    onSensorUpdate: (d) => seen.push(d),
    onHealthUpdate: () => {},
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  instances[0]._dispatch("sensor_update", { temperature: 22.5, humidity: 55 });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].temperature, 22.5);
  sub.close();
});


test("subscribe: health_update routes to onHealthUpdate", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const seen = [];
  const sub = subscribe({
    onEffectorState: () => {},
    onSensorUpdate: () => {},
    onHealthUpdate: (d) => seen.push(d),
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  instances[0]._dispatch("health_update", { status: "healthy" });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].status, "healthy");
  sub.close();
});


test("subscribe: fan_status routes to onFanStatus", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const seen = [];
  const sub = subscribe({
    onEffectorState: () => {},
    onSensorUpdate: () => {},
    onHealthUpdate: () => {},
    onFanStatus: (d) => seen.push(d),
    EventSourceCtor: Ctor,
  });
  instances[0]._dispatch("fan_status", { state: "on" });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].state, "on");
  sub.close();
});


test("subscribe: close() closes the underlying EventSource", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const sub = subscribe({
    onEffectorState: () => {},
    onSensorUpdate: () => {},
    onHealthUpdate: () => {},
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  assert.equal(instances[0].closed, false);
  sub.close();
  assert.equal(instances[0].closed, true);
});


test("subscribe: missing callbacks are tolerated (no throw on dispatch)", () => {
  // The page boot may not care about every event — the wrapper must
  // not throw if a callback is omitted.
  const { Ctor, instances } = _makeFakeEventSource();
  const sub = subscribe({
    onEffectorState: () => {},
    // onSensorUpdate intentionally omitted
    onHealthUpdate: () => {},
    onFanStatus: () => {},
    EventSourceCtor: Ctor,
  });
  // Dispatching the un-subscribed event must NOT throw.
  instances[0]._dispatch("sensor_update", { temperature: 1 });
  sub.close();
});


test("subscribe: error event closes + reconnects with 5s backoff", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  // Patch setTimeout so the test doesn't actually wait.
  const scheduled = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => {
    scheduled.push({ fn, ms });
    return scheduled.length; // fake handle
  };
  try {
    const sub = subscribe({
      onEffectorState: () => {},
      onSensorUpdate: () => {},
      onHealthUpdate: () => {},
      onFanStatus: () => {},
      EventSourceCtor: Ctor,
    });
    // Fire the error handler the wrapper installed.
    instances[0].onerror();
    // First reconnect attempt scheduled at 5000ms.
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0].ms, 5000);
    // Original closed.
    assert.equal(instances[0].closed, true);
    // Run the scheduled reconnect.
    scheduled[0].fn();
    // A new EventSource was constructed.
    assert.equal(instances.length, 2);
    assert.equal(instances[1].url, "/api/stream");
    sub.close();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});


test("subscribe: repeated errors double backoff up to 60s cap", () => {
  const { Ctor, instances } = _makeFakeEventSource();
  const scheduled = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => {
    scheduled.push({ fn, ms });
    return scheduled.length;
  };
  try {
    const sub = subscribe({
      onEffectorState: () => {},
      onSensorUpdate: () => {},
      onHealthUpdate: () => {},
      onFanStatus: () => {},
      EventSourceCtor: Ctor,
    });
    // Trigger four consecutive failed reconnects.
    instances[0].onerror();           // schedules 5s
    scheduled[scheduled.length - 1].fn();
    instances[1].onerror();           // schedules 10s
    scheduled[scheduled.length - 1].fn();
    instances[2].onerror();           // schedules 20s
    scheduled[scheduled.length - 1].fn();
    instances[3].onerror();           // schedules 40s
    scheduled[scheduled.length - 1].fn();
    instances[4].onerror();           // schedules 60s (cap)
    scheduled[scheduled.length - 1].fn();
    instances[5].onerror();           // stays at 60s

    const delays = scheduled.map((s) => s.ms);
    assert.deepEqual(delays, [5000, 10000, 20000, 40000, 60000, 60000]);
    sub.close();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});


test("subscribe: successful re-open resets the backoff to 5s", () => {
  // After an `open` event (clean reconnect) the next failure must
  // restart the backoff at 5s rather than continuing the climb.
  const { Ctor, instances } = _makeFakeEventSource();
  const scheduled = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => {
    scheduled.push({ fn, ms });
    return scheduled.length;
  };
  try {
    const sub = subscribe({
      onEffectorState: () => {},
      onSensorUpdate: () => {},
      onHealthUpdate: () => {},
      onFanStatus: () => {},
      EventSourceCtor: Ctor,
    });
    // Fail once → 5s scheduled.
    instances[0].onerror();
    scheduled[scheduled.length - 1].fn();
    // Fail again → 10s scheduled.
    instances[1].onerror();
    scheduled[scheduled.length - 1].fn();
    // Successful reconnect signals via onopen.
    if (typeof instances[2].onopen === "function") instances[2].onopen();
    // Next failure should restart the backoff at 5s.
    instances[2].onerror();
    const lastDelay = scheduled[scheduled.length - 1].ms;
    assert.equal(lastDelay, 5000);
    sub.close();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }
});
