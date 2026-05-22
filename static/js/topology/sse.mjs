/**
 * SSE wrapper for the topology page (Phase 10 Task 10.1).
 *
 * Subscribes a single EventSource against /api/stream and routes the
 * four named events the topology page cares about onto user-supplied
 * callbacks:
 *
 *   - effector_state_changed → onEffectorState({id, state, auto})
 *   - sensor_update          → onSensorUpdate(reading)
 *   - health_update          → onHealthUpdate(health)
 *   - fan_status             → onFanStatus(status)  (legacy single-fan)
 *
 * Each handler JSON-parses `e.data` and routes to the matching callback.
 * Missing callbacks are tolerated (dispatched events for those simply
 * no-op) so the page boot can subscribe to a subset of events.
 *
 * Auto-reconnect: on an EventSource `error` the wrapper closes the
 * current connection and reopens after a backoff (5s base, doubling to
 * a 60s cap). A successful `open` resets the backoff so a transient
 * blip doesn't permanently stretch reconnects. Matches the pattern in
 * static/js/dashboard.js — see `connectSSE` there.
 *
 * Tests inject an `EventSourceCtor` so the module works under Node
 * without the browser API present.
 */

const BACKOFF_BASE_MS = 5000;
const BACKOFF_CAP_MS = 60000;


/**
 * Subscribe to the SSE channel and dispatch the four topology events.
 *
 * @param {object} args
 * @param {(d: object) => void} [args.onEffectorState]
 * @param {(d: object) => void} [args.onSensorUpdate]
 * @param {(d: object) => void} [args.onHealthUpdate]
 * @param {(d: object) => void} [args.onFanStatus]
 * @param {*} [args.EventSourceCtor=EventSource] Stubbed in tests.
 * @returns {{close: () => void}}
 */
export function subscribe({
  onEffectorState,
  onSensorUpdate,
  onHealthUpdate,
  onFanStatus,
  EventSourceCtor,
} = {}) {
  const Ctor = EventSourceCtor
    || (typeof EventSource !== "undefined" ? EventSource : null);
  if (!Ctor) {
    // No EventSource available (e.g. older Safari with the API
    // disabled) — return a no-op handle so the boot can keep wiring
    // the rest of the page.
    return { close() {} };
  }

  let current = null;
  let backoffMs = BACKOFF_BASE_MS;
  let closedByCaller = false;
  let reconnectHandle = null;

  function _safeInvoke(fn, payload) {
    if (typeof fn !== "function") return;
    try {
      fn(payload);
    } catch (_e) {
      // Swallow callback errors so one bad handler can't tear down
      // the whole subscription. Production logging happens via the
      // page-level boot (this module is intentionally side-effect-light).
    }
  }

  function _open() {
    current = new Ctor("/api/stream");

    const _route = (handler) => (ev) => {
      let payload;
      try {
        payload = JSON.parse(ev.data);
      } catch (_e) {
        return;
      }
      _safeInvoke(handler, payload);
    };

    current.addEventListener("effector_state_changed", _route(onEffectorState));
    current.addEventListener("sensor_update",          _route(onSensorUpdate));
    current.addEventListener("health_update",          _route(onHealthUpdate));
    current.addEventListener("fan_status",             _route(onFanStatus));

    current.onopen = () => {
      // Clean reconnect — reset the backoff so the next failure
      // restarts at the base delay.
      backoffMs = BACKOFF_BASE_MS;
    };

    current.onerror = () => {
      if (closedByCaller) return;
      try {
        current.close();
      } catch (_e) { /* ignore */ }
      const delay = backoffMs;
      // Double the backoff for the NEXT failure, capped at 60s.
      backoffMs = Math.min(backoffMs * 2, BACKOFF_CAP_MS);
      reconnectHandle = setTimeout(() => {
        if (closedByCaller) return;
        _open();
      }, delay);
    };
  }

  _open();

  return {
    close() {
      closedByCaller = true;
      if (reconnectHandle) {
        try { clearTimeout(reconnectHandle); } catch (_e) { /* ignore */ }
        reconnectHandle = null;
      }
      if (current) {
        try { current.close(); } catch (_e) { /* ignore */ }
      }
    },
  };
}
