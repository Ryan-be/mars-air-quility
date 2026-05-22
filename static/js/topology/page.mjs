/**
 * /controls page orchestrator (Phase 4 Task 4.2).
 *
 * Boot sequence:
 *   1. Locate the four host elements stamped into templates/controls.html
 *      (tp-topbar-host / tp-graph-host / tp-statusbar-host / tp-sidepanel-host).
 *      Missing hosts are tolerated — the boot logs + skips so a future
 *      template change can't black-screen the whole page.
 *   2. Fetch GET /api/topology and paint placeholder chrome into the
 *      topbar (brand + page label), statusbar (SSE indicator), and graph
 *      (empty <svg.tp-graph-svg> mount point that Phase 5 will fill).
 *   3. On fetch failure, surface a single statusbar error message rather
 *      than throwing — the SSE wiring (Phase 10) still needs to run.
 *
 * The function is exported AND auto-runs in a browser via the bottom
 * guard so tests can call boot() directly with a stub fetch + stub
 * EventSource. Phase 5/6 swap the placeholders for the real graph and
 * node cards; this module is the glue, not the renderer.
 */

import { fetchTopology } from "./api.mjs";


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

  // Mount the empty SVG canvas Phase 5 will render edges + nodes into.
  // Done unconditionally so the test for "graph host gets the
  // placeholder svg" passes even when /api/topology 500s.
  if (graph) {
    graph.innerHTML =
      `<svg class="tp-graph-svg" xmlns="http://www.w3.org/2000/svg"></svg>`;
  }

  try {
    await fetchTopology(fetchFn);
  } catch (exc) {
    if (statusbar) {
      statusbar.innerHTML =
        `<span class="tp-status-pill tp-status-pill-err">` +
        `Failed to load topology: ${exc.message}</span>`;
    }
    // Swallow — Phase 10 SSE wiring still needs to attempt its
    // EventSource connection so a brief 5xx blip doesn't take down
    // the whole module-load chain.
  }
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
