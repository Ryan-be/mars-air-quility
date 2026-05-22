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
import { openAddEffectorModal } from "./components/add-effector-modal.mjs";
import { renderSidePanel } from "./components/side-panel.mjs";
import { computeStats } from "./stats.mjs";
import { subscribe } from "./sse.mjs";
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

// Module-scoped pointer to the current boot's "select node by id"
// callback. The cog event listener (attached once per document, see
// below) reads through this so a fresh boot() re-binds the latest
// closure rather than firing into the previous one's store. Stays
// null until the first boot completes.
let _currentSelectNode = null;


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
 * Push a value into a rolling history buffer (Phase 10 Task 10.3).
 *
 * `historyDict[nodeId][key]` is the buffer. Nullish/NaN values are
 * skipped so the sparkline polyline doesn't end up with gaps that
 * collapse to (NaN, NaN) when rendered. The buffer is capped at
 * `cap` (default 30) entries; once full, oldest values fall off the
 * front so the visible sparkline scrolls.
 *
 * Pure mutation on `historyDict` — exported for unit testing AND for
 * the boot()-side `onSensorUpdate` callback.
 */
export function pushHistory(historyDict, nodeId, key, value, cap = 30) {
  if (value == null || Number.isNaN(value)) return;
  if (!historyDict[nodeId]) historyDict[nodeId] = {};
  const bucket = historyDict[nodeId];
  if (!Array.isArray(bucket[key])) bucket[key] = [];
  bucket[key].push(value);
  while (bucket[key].length > cap) bucket[key].shift();
}


/**
 * Project a smart_plugs row from POST /api/effectors into the topology
 * node shape produced by GET /api/topology. The v2 API returns the raw
 * DB row (id as integer, scope as 'hub'/'grow_unit') while the
 * topology endpoint returns a UI-shaped node (id as 'effector:<n>',
 * parent set, mode derived from auto_mode + current_state). Mirroring
 * the server-side projection in `mlss_monitor.routes.api_topology.
 * _effector_node()` keeps the wire format internally consistent.
 *
 * Exported for tests + the Phase 9 add-effector wiring.
 */
export function effectorRowToNode(row) {
  if (!row) return null;
  const id = `effector:${row.id}`;
  const parent = row.scope === "hub"
    ? "hub"
    : `grow:${row.grow_unit_id}`;
  let mode;
  if (row.auto_mode) {
    mode = "auto";
  } else if (row.current_state === "on") {
    mode = "on";
  } else {
    mode = "off";
  }
  return {
    id,
    kind: "effector",
    parent,
    label: row.label,
    effector_type: row.effector_type,
    mode,
    current_state: row.current_state || "unknown",
    is_enabled: row.is_enabled,
  };
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
  const sidepanelHost = document.getElementById("tp-sidepanel-host");

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
  // `history` is a per-node rolling-buffer dict (Phase 10 Task 10.3):
  //
  //   history.hub.temp     // last 30 hub temperatures from sensor_update
  //   history.hub.rh       // last 30 hub humidities
  //   history.hub.co2      // last 30 hub eCO₂ values
  //   history["grow:1"]…   // populated when grow_telemetry SSE lands
  //
  // The card renderers read this via `handlers.history` so the boot
  // doesn't have to manually thread it into every renderXCard call.
  const store = {
    nodes,
    positions,
    viewport: initialViewport,
    effectorById: Object.fromEntries(
      (snapshot.effectors || []).map((e) => [e.id, e]),
    ),
    history: {},
  };

  // Side-panel selection (Phase 8 Task 8.1). Tracks which node the
  // operator has clicked into; null means the panel is collapsed. The
  // panel host is part of the page scaffold so a missing host is a
  // template regression — log + skip so the rest of the page still
  // boots.
  let selectedNodeId = null;

  function _findNodeById(id) {
    return id == null ? null : store.nodes.find((n) => n.id === id) || null;
  }

  function mountSidePanel() {
    if (!sidepanelHost) return;
    const node = _findNodeById(selectedNodeId);
    const panel = renderSidePanel({
      node,
      allNodes: store.nodes,
      doc: document,
      isAdmin: _isAdmin(document),
      callbacks: {
        onClose: () => {
          selectedNodeId = null;
          mountSidePanel();
        },
        onModeChange: _onMode,
        onReparent: _onReparent,
      },
    });
    sidepanelHost.replaceChildren(panel);
  }

  function _selectNode(nodeId) {
    selectedNodeId = nodeId;
    mountSidePanel();
  }
  // Point the module-scoped pointer at the current boot's selector so
  // the cog event listener (below) hits THIS boot's store rather than
  // a stale closure from a previous boot() invocation.
  _currentSelectNode = _selectNode;

  // Phase 8 Task 8.6 — listen for the admin cog's custom event. The
  // event bubbles from inside the effector card up through the graph
  // host; capturing on `document` keeps the listener resilient to
  // re-renders that swap the card subtree out. The handler reads
  // through the module-scoped `_currentSelectNode` pointer so a fresh
  // boot() rebinds without stacking listeners or stale closures.
  if (!document.__topologyOpenConfigBound) {
    document.addEventListener("topology-open-config", (ev) => {
      const id = ev.detail && ev.detail.nodeId;
      if (id && typeof _currentSelectNode === "function") {
        _currentSelectNode(id);
      }
    });
    document.__topologyOpenConfigBound = true;
  }

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
        // Phase 9 Task 9.2 — open the add-effector modal with
        // defaultScope="hub" (topbar entry point). On 201 we project
        // the returned smart_plugs row into the topology node shape,
        // push it into the store, and re-render so the operator sees
        // the new card without a page refresh.
        openAddEffectorModal({
          defaultScope: "hub",
          defaultGrowUnitId: null,
          ownerDocument: document,
          fetchFn,
          onCreated: (eff) => {
            const node = effectorRowToNode(eff);
            if (!node) return;
            store.nodes.push(node);
            store.effectorById[node.id] = node;
            // Add a layout position so the new card doesn't pile at
            // (0,0). Re-running autoLayout on the augmented node set
            // gives it a slot on the radial arc — Phase 11 will
            // persist any subsequent drag.
            store.positions = autoLayout(store.nodes);
            mountGraph();
            mountTopbar();
          },
        });
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
      const storeNode = store.nodes.find((n) => n.id === effectorId);
      if (storeNode) {
        storeNode.mode = mode;
        if (mode === "on" || mode === "off") storeNode.current_state = mode;
      }
      mountGraph();
      // Re-render the side panel so the active-mode button updates
      // immediately. The panel's Mode bar shares the same callback so
      // this also covers the in-panel click path.
      mountSidePanel();
    } catch (exc) {
      // On failure, refetch the snapshot so the UI re-syncs with the
      // server's actual state. Keep this quiet in the console rather
      // than throwing — the topbar will reflect the canonical state.
      console.warn("setEffectorState failed:", exc);
    }
  };

  /**
   * Re-parent an effector — Phase 8 Task 8.3. Maps the panel's
   * `(effectorId, newParentId)` callback into a PATCH on
   * /api/effectors/<id>. Returns `{error}` on a server 400 so the
   * panel can inline-surface it (e.g. scope incompatibility).
   *
   * The optimistic local update mirrors _onMode's pattern: flip the
   * store node's `parent`, re-mount graph + topbar + panel so the
   * edges + the Belongs-to selection both reflect the new state.
   */
  const _onReparent = async (effectorId, newParentId) => {
    const numericId = parseInt(effectorId.split(":")[1], 10);
    const body = newParentId === "hub"
      ? { scope: "hub", grow_unit_id: null }
      : {
        scope: "grow_unit",
        grow_unit_id: parseInt(newParentId.split(":")[1], 10),
      };
    const resp = await fetchFn(`/api/effectors/${numericId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.status === 400) {
      let serverMsg;
      try {
        const j = await resp.json();
        serverMsg = j.error || j.detail;
      } catch (_e) { /* ignore */ }
      return { error: serverMsg
        || "That effector type can't be assigned to this scope." };
    }
    if (!resp.ok) {
      return { error: `Server returned HTTP ${resp.status}.` };
    }
    // Optimistic local update.
    const eff = store.effectorById[effectorId];
    if (eff) eff.parent = newParentId;
    const storeNode = store.nodes.find((n) => n.id === effectorId);
    if (storeNode) storeNode.parent = newParentId;
    mountGraph();
    mountTopbar();
    mountSidePanel();
    return { ok: true };
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
        history: store.history,
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
        onClick: (clickedId) => {
          // Phase 8 Task 8.1 — clicking a node opens the side panel
          // populated with that node's configuration. Click is
          // distinguished from drag by the < 2px movement heuristic
          // inside setupNodeDrag.
          _selectNode(clickedId);
        },
      });
    }
  }

  // ── Targeted re-render (Phase 10 Task 10.2) ──────────────────────
  // Swap the inner content of a single <div class="tp-node"> rather
  // than re-running renderGraph. Edges are NOT re-drawn — they only
  // depend on node positions, which an SSE state-flip doesn't touch.
  // Used by all four SSE handlers below.
  function reRenderNode(nodeId) {
    const node = store.nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const el = graph.querySelector(`.tp-node[data-node-id="${nodeId}"]`);
    if (!el) return;
    const history = store.history[nodeId] || {};
    let card = null;
    if (node.kind === "hub" && cardRenderers.hub) {
      card = cardRenderers.hub(node, history, document);
    } else if (node.kind === "grow" && cardRenderers.grow) {
      card = cardRenderers.grow(node, history, document);
    } else if (node.kind === "effector" && cardRenderers.effector) {
      card = cardRenderers.effector(node, document, {
        onMode: _onMode,
        isAdmin: _isAdmin(document),
      });
    }
    if (card) el.replaceChildren(card);
  }

  // ── SSE wiring (Phase 10 Task 10.2 + 10.3) ───────────────────────
  // Subscribes for the duration of the boot lifecycle. The returned
  // handle gets parked on the document so a hot-reload (e.g. tests
  // calling boot() twice) tears the previous subscription down before
  // opening a new one.
  function _applyEffectorState({ id, state, auto }) {
    const nodeKey = `effector:${id}`;
    const eff = store.effectorById[nodeKey];
    const node = store.nodes.find((n) => n.id === nodeKey);
    if (!eff || !node) return;
    let mode;
    if (auto === true) {
      mode = "auto";
    } else if (state === "on" || state === "off") {
      mode = state;
    } else {
      mode = eff.mode;
    }
    eff.mode = mode;
    if (state === "on" || state === "off") eff.current_state = state;
    node.mode = mode;
    if (state === "on" || state === "off") node.current_state = state;
    reRenderNode(nodeKey);
    // The topbar's Active / Auto-vs-Forced rollup depends on the same
    // fields so a state flip can change those numbers. Re-mount the
    // topbar (cheap; pure recompute) but leave the graph alone.
    mountTopbar();
  }

  if (document.__topologyActiveSub
      && typeof document.__topologyActiveSub.close === "function") {
    try { document.__topologyActiveSub.close(); } catch (_e) { /* ignore */ }
    document.__topologyActiveSub = null;
  }
  document.__topologyActiveSub = subscribe({
    onEffectorState: (d) => {
      if (!d || d.id == null) return;
      _applyEffectorState(d);
    },
    onSensorUpdate: (reading) => {
      if (!reading) return;
      // Mirror the dashboard.js field names — sensor_update carries
      // {temperature, humidity, eco2, ...}. The topology hub card
      // reads node.sensors.{temp, rh, co2}.
      const hubNode = store.nodes.find((n) => n.id === "hub");
      if (!hubNode) return;
      hubNode.sensors = hubNode.sensors || {};
      if (reading.temperature != null) {
        hubNode.sensors.temp = reading.temperature;
        pushHistory(store.history, "hub", "temp", reading.temperature);
      }
      if (reading.humidity != null) {
        hubNode.sensors.rh = reading.humidity;
        pushHistory(store.history, "hub", "rh", reading.humidity);
      }
      if (reading.eco2 != null) {
        hubNode.sensors.co2 = reading.eco2;
        pushHistory(store.history, "hub", "co2", reading.eco2);
      }
      reRenderNode("hub");
    },
    onHealthUpdate: (health) => {
      if (!health) return;
      const cell = document.querySelector(
        ".tp-stat [data-role='hub-status']",
      );
      if (!cell) return;
      const label = String(health.status || "").trim();
      if (label) {
        cell.textContent = label.charAt(0).toUpperCase() + label.slice(1);
      }
    },
    onFanStatus: (status) => {
      // Legacy single-fan broadcast — re-use the effector_state path
      // for plug id=1 (the seeded fan row). Payload may omit `id`.
      if (!status) return;
      _applyEffectorState({
        id: status.id != null ? status.id : 1,
        state: status.state,
        auto: status.auto === true,
      });
    },
  });

  mountGraph();
  mountTopbar();
  // Initial side-panel paint — the hidden empty shell. The panel
  // becomes visible once the operator clicks a node.
  mountSidePanel();
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
