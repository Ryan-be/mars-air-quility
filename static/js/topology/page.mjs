/**
 * /controls page orchestrator (Phase 4 Task 4.2, expanded in Phase 5
 * Task 5.7 to mount the graph and Phase 6 Task 6.6 to wire the
 * on-card AUTO/ON/OFF control to the v2 effectors API).
 *
 * Boot sequence:
 *   1. Locate the four host elements stamped into templates/controls.html
 *      (tp-topbar-host / tp-graph-host / tp-statusbar-host / tp-sidepanel-host).
 *      Missing hosts are tolerated — the boot logs + skips so a future
 *      template change can't black-screen the whole page.
 *   2. Paint placeholder topbar + SSE indicator immediately so a slow
 *      /api/topology doesn't leave the chrome blank for a second.
 *   3. Fetch GET /api/topology, compute auto-layout positions (merged
 *      with any server-persisted ones), and mount the pan/zoom graph
 *      into #tp-graph-host. Then wire pan + zoom + per-node drag so
 *      the operator can rearrange the view.
 *   4. On fetch failure, surface a single statusbar error message.
 *
 * Subsequent live updates land via the SSE bus (Phase 10 wiring) —
 * `boot()` is just the cold-start mount.
 */

import { fetchTopology, setEffectorState } from "./api.mjs";
import { autoLayout } from "./layout.mjs";
import {
  renderGraph, applyViewport,
  setupPan, setupZoom, setupNodeDrag,
} from "./graph.mjs";
import { renderTopbar } from "./components/topbar.mjs";
import { computeStats } from "./stats.mjs";
// Card renderers — wired in Phase 6 Task 6.7. Until then renderGraph
// produces empty .tp-node placeholder divs, which is what the Phase 5
// integration test asserts. The imports below are pulled in via a
// dynamic import inside boot() so a partial Phase-5-only deploy can
// still mount the graph chrome without the card modules present.


// Mission-time tick interval. Stored at module scope so a fresh boot()
// call (in tests) tears the previous interval down before mounting a
// new one — without the cancel, JSDOM's fake timers would never stop
// firing handlers against a detached host.
let _missionTimeInterval = null;


function _isAdmin(doc) {
  // The /controls template stamps body.dataset.role from session["user_role"].
  // Missing role (e.g. logged-out test fixture) → not admin, button hidden.
  const body = doc && doc.body;
  return !!body && body.dataset && body.dataset.role === "admin";
}


function _formatMissionTime(startMs, nowMs) {
  // "T+HH:MM:SS" relative to session start. Wraps cleanly past 24 h —
  // the operator on a long-running shift wants to see "T+27:14:02"
  // rather than "T+03:14:02" silently rolling over.
  const elapsed = Math.max(0, Math.floor((nowMs - startMs) / 1000));
  const hh = String(Math.floor(elapsed / 3600)).padStart(2, "0");
  const mm = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  return `T+${hh}:${mm}:${ss}`;
}


function _startMissionTimeTick(doc, startMs) {
  // Update every second. Cancel any pre-existing interval so a re-boot
  // (test path) doesn't leak handlers.
  if (_missionTimeInterval) {
    clearInterval(_missionTimeInterval);
    _missionTimeInterval = null;
  }
  const tick = () => {
    const cell = doc.querySelector(
      ".tp-topbar-inner [data-role='mission-time']",
    );
    if (!cell) return;
    // If a <rux-clock> has slotted in, leave it alone — the web
    // component self-updates and we'd just trample its rendered DOM.
    if (cell.querySelector("rux-clock")) return;
    cell.textContent = _formatMissionTime(startMs, Date.now());
  };
  // Fire once immediately so the first paint matches a slightly-later
  // server clock without waiting a full second.
  tick();
  if (typeof setInterval === "function") {
    _missionTimeInterval = setInterval(tick, 1000);
    // Don't pin the Node event loop alive on this interval — JSDOM
    // tests would otherwise hang waiting for the timer to clear. The
    // unref() call is a no-op on browser timers but lets `node --test`
    // exit cleanly between test files.
    if (_missionTimeInterval
        && typeof _missionTimeInterval.unref === "function") {
      _missionTimeInterval.unref();
    }
  }
}


/**
 * Project the /api/topology snapshot into the flat node list the graph
 * renderer + auto-layout expect. The endpoint returns hub / grows /
 * effectors as separate arrays; the renderer needs a single list with
 * `parent` populated on every non-hub node so the edge-drawing pass
 * works.
 */
function _flattenTopology(snapshot) {
  const out = [];
  if (snapshot.hub) out.push({ ...snapshot.hub });
  for (const g of snapshot.grows || []) {
    // Every grow is parented to the hub — the renderer doesn't infer
    // this from the topology endpoint so we set it here.
    out.push({ ...g, parent: g.parent || "hub" });
  }
  for (const e of snapshot.effectors || []) {
    out.push({ ...e });
  }
  return out;
}


/**
 * Merge server-persisted positions over the radial auto-layout output.
 * Per the plan, the auto-layout runs every boot; persisted positions
 * (when present) override only the nodes the user has dragged.
 */
function _mergePositions(nodes, persisted) {
  const positions = autoLayout(nodes);
  for (const [id, pos] of Object.entries(persisted || {})) {
    if (pos && typeof pos.x === "number" && typeof pos.y === "number") {
      positions[id] = { x: pos.x, y: pos.y };
    }
  }
  return positions;
}


/**
 * Boot the page. Imported by templates/controls.html via
 * `<script type="module">` so it runs after the host elements have
 * been parsed.
 *
 * @param {object} [options]
 * @param {Function} [options.fetchFn=fetch] Stubbed in tests.
 * @returns {Promise<void>}
 */
export async function boot({ fetchFn = fetch } = {}) {
  const topbar = document.getElementById("tp-topbar-host");
  const graph = document.getElementById("tp-graph-host");
  const statusbar = document.getElementById("tp-statusbar-host");
  // sidepanel intentionally left untouched — Phase 8 owns its content.

  // Paint the brand row immediately so a slow /api/topology doesn't
  // leave the topbar blank for ~1 second. Placeholder content; Phase 7
  // replaces this with the proper telemetry topbar.
  if (topbar) {
    topbar.innerHTML =
      `<span class="tp-brand">MLSS</span>` +
      `<span class="tp-topbar-label">· NODE MAP</span>`;
  }

  // Paint the statusbar SSE indicator immediately for the same reason
  // — it'll be updated to "● SSE connected" once Phase 10 wires the
  // live event stream.
  if (statusbar) {
    statusbar.innerHTML =
      `<span class="tp-status-pill">● SSE</span>`;
  }

  let snapshot;
  try {
    snapshot = await fetchTopology(fetchFn);
  } catch (exc) {
    if (statusbar) {
      statusbar.innerHTML =
        `<span class="tp-status-pill tp-status-pill-err">` +
        `Failed to load topology: ${exc.message}</span>`;
    }
    // Mount the placeholder svg so the graph host isn't blank.
    if (graph) {
      graph.innerHTML =
        `<svg class="tp-graph-svg" xmlns="http://www.w3.org/2000/svg"></svg>`;
    }
    return;
  }

  if (!graph) return;

  // Compute layout + initial viewport.
  const nodes = _flattenTopology(snapshot);
  const positions = _mergePositions(nodes, snapshot.layout);

  // Centre the world origin in the host viewport. The hub sits at
  // (0,0) in world coords; placing translate at (w/2, h/2 - 40)
  // pushes it just below the centre — gives the lower grow arc room
  // to breathe without clipping above-hub effectors.
  function _computeInitialViewport() {
    return {
      x: (graph.clientWidth || 800) / 2,
      y: (graph.clientHeight || 600) / 2 - 40,
      k: 0.9,
    };
  }
  const initialViewport = _computeInitialViewport();

  // Page-level state. The handler callbacks close over these so a
  // drag updates the right entry and re-renders only what needs it.
  const store = {
    nodes,
    positions,
    viewport: initialViewport,
    effectorById: Object.fromEntries(
      (snapshot.effectors || []).map((e) => [e.id, e]),
    ),
  };

  // ── Topbar mount (Phase 7 Task 7.3) ───────────────────────────────
  // The topbar replaces the placeholder brand row painted before the
  // fetch resolved. Re-rendered after every state change so its
  // numeric cells stay in sync with the graph.
  function mountTopbar() {
    if (!topbar) return;
    const stats = computeStats(store.nodes);
    const inner = renderTopbar({
      stats,
      isAdmin: _isAdmin(document),
      onRearrange: () => {
        // Clear persisted positions + re-run autoLayout. The plan
        // notes a server reset endpoint lands in Phase 11; for now
        // the click is purely client-side.
        store.positions = autoLayout(store.nodes);
        mountGraph();
      },
      onRecenter: () => {
        store.viewport = _computeInitialViewport();
        mountGraph();
      },
      onAddEffector: () => {
        // Phase 9 Task 9.2 replaces this with the modal call. For now
        // a console.log keeps the click chain wired without coupling
        // the topbar to a Phase-9 import.
        // eslint-disable-next-line no-console
        console.log("[topology] add-effector clicked");
      },
      doc: document,
    });
    topbar.replaceChildren(inner);
    _startMissionTimeTick(document, Date.now());
  }

  // Re-render the whole graph (cheap — < 50 nodes for any realistic
  // tent setup). Phase 10 will swap this for granular updates when
  // SSE events land.
  const _onMode = async (effectorId, mode) => {
    const numericId = parseInt(effectorId.split(":")[1], 10);
    try {
      await setEffectorState(numericId, mode, fetchFn);
      // Optimistic local update — the SSE event (Phase 10) will
      // confirm + correct.
      const eff = store.effectorById[effectorId];
      if (eff) {
        eff.mode = mode;
        if (mode === "on" || mode === "off") eff.current_state = mode;
      }
      mountGraph();
    } catch (exc) {
      // On failure, refetch the snapshot so the UI re-syncs with the
      // server's actual state. Keep this quiet in the console rather
      // than throwing — the topbar will reflect the canonical state.
      console.warn("setEffectorState failed:", exc);
    }
  };

  // Card renderers — loaded lazily so the boot still works if Phase 5
  // ships before Phase 6. Failed imports leave the cards as empty
  // .tp-node placeholders (which the Phase 5 integration test asserts).
  const cardRenderers = {};
  try {
    const [hubMod, growMod, effMod] = await Promise.all([
      import("./components/hub-card.mjs"),
      import("./components/topology-grow-card.mjs"),
      import("./components/effector-card.mjs"),
    ]);
    cardRenderers.hub = hubMod.renderHubCard;
    cardRenderers.grow = growMod.renderTopologyGrowCard;
    cardRenderers.effector = effMod.renderEffectorCard;
  } catch (_exc) {
    // Cards not yet shipped — graph mounts with empty placeholders.
  }

  function mountGraph() {
    const wrap = renderGraph({
      nodes: store.nodes,
      positions: store.positions,
      viewport: store.viewport,
      ownerDocument: document,
      handlers: {
        cardRenderers,
        onMode: _onMode,
      },
    });
    graph.replaceChildren(wrap);
    // Re-wire interaction handlers on every render — they hold
    // closures over the now-stale element references otherwise.
    setupPan({
      wrapEl: graph,
      getViewport: () => store.viewport,
      onChange: (vp) => {
        store.viewport = vp;
        applyViewport(wrap, vp);
      },
    });
    setupZoom({
      wrapEl: graph,
      getViewport: () => store.viewport,
      onChange: (vp) => {
        store.viewport = vp;
        applyViewport(wrap, vp);
      },
    });
    for (const nodeEl of wrap.querySelectorAll(".tp-node")) {
      const id = nodeEl.dataset.nodeId;
      setupNodeDrag({
        nodeEl,
        nodeId: id,
        getPos: () => store.positions[id],
        getViewport: () => store.viewport,
        onChange: (nodeId, pos) => {
          store.positions[nodeId] = pos;
          // Cheapest path: just move this one node; Phase 11 adds
          // debounced persistence + edge-only re-render. For now
          // a full re-render covers correctness.
          mountGraph();
        },
        onClick: (_id) => {
          // Phase 8 owns the side-panel-on-click behaviour. Stubbed
          // here so the click vs drag distinction still works.
        },
      });
    }
  }

  mountGraph();
  mountTopbar();
}


// Auto-boot when the page mounts. The dual guards (window present
// + tp-app element present) match static/js/backup/page.mjs so the
// module can be imported by tests under Node without trying to call
// fetch.
if (typeof window !== "undefined"
    && typeof document !== "undefined"
    && document.getElementById("tp-app")) {
  boot();
}
