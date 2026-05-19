/**
 * /admin/backup page orchestrator.
 *
 * On boot:
 *   1. Fetch GET /api/admin/backup/config
 *   2. Fetch GET /api/admin/backup/status
 *   3. Render the three sections (status panel, settings form, advanced
 *      controls) into the host elements declared in admin_backup.html.
 *   4. Wire the "Save settings" submit → PUT /config + refresh status.
 *   5. Open an EventSource to /api/stream and pipe
 *      `backup_status_changed` events into statusPanel.update(...).
 *
 * The components own all their internal state — page.mjs is just the
 * glue. Tests cover the components directly; this file is exercised
 * by the live page.
 */

import { getConfig, putConfig, getStatus } from "./api.mjs";
import { renderStatusPanel }
  from "./components/status-panel.mjs";
import { renderSettingsForm }
  from "./components/settings-form.mjs";
import { renderAdvancedControls }
  from "./components/advanced-controls.mjs";


/**
 * Boot the page. Imported by admin_backup.html via
 * `<script type="module">` so it runs after the host elements have
 * been parsed.
 */
export async function boot() {
  const hostStatus = document.getElementById("bk-status-host");
  const hostForm = document.getElementById("bk-settings-host");
  const hostAdv = document.getElementById("bk-advanced-host");
  const saveBtn = document.getElementById("bk-save-btn");
  const saveStatus = document.getElementById("bk-save-status");

  // Two initial fetches in parallel — the page paints in one tick.
  let cfg, status;
  try {
    [cfg, status] = await Promise.all([getConfig(), getStatus()]);
  } catch (exc) {
    hostStatus.innerHTML =
      `<p class="status-err">Failed to load backup config: ${exc.message}</p>`;
    return;
  }

  // -- Render the three sections ---------------------------------------
  const statusPanel = renderStatusPanel({ status, ownerDocument: document });
  hostStatus.replaceChildren(statusPanel);

  const settingsForm = renderSettingsForm({
    config: cfg, ownerDocument: document,
  });
  hostForm.replaceChildren(settingsForm);

  const advanced = renderAdvancedControls({
    paused: !!status.paused, ownerDocument: document,
  });
  hostAdv.replaceChildren(advanced);

  // -- Save button: PUT /config + refresh ------------------------------
  saveBtn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    saveBtn.disabled = true;
    saveStatus.textContent = "Saving…";
    saveStatus.className = "";
    try {
      const payload = settingsForm.serialize();
      await putConfig(payload);
      saveStatus.textContent = "Saved. Worker reconciled.";
      saveStatus.className = "status-ok";
      // Re-fetch status so the panel reflects the worker reconcile
      // (enabling a pipeline starts a thread which we want to see).
      const fresh = await getStatus();
      const next = renderStatusPanel({
        status: fresh, ownerDocument: document,
      });
      hostStatus.replaceChildren(next);
      advanced.setPaused(!!fresh.paused);
    } catch (exc) {
      saveStatus.textContent = `Failed: ${exc.message}`;
      saveStatus.className = "status-err";
    } finally {
      saveBtn.disabled = false;
    }
  });

  // -- SSE: backup_status_changed → live updates -----------------------
  // The single shared /api/stream broadcasts every event from the bus.
  // We listen for the backup-specific event and forward the snapshot
  // to the status panel.
  try {
    const sse = new EventSource("/api/stream");
    sse.addEventListener("backup_status_changed", (msg) => {
      try {
        const data = JSON.parse(msg.data);
        // The current statusPanel may have been replaced by the Save
        // handler above — query the host's first child each time.
        const panel = hostStatus.firstElementChild;
        if (panel && typeof panel.update === "function") {
          panel.update(data.pipeline, data);
        }
      } catch (exc) {
        console.warn("malformed backup_status_changed payload", exc);
      }
    });
    sse.onerror = () => {
      // EventSource auto-reconnects; just log.
      console.warn("backup SSE connection error (will auto-reconnect)");
    };
  } catch (exc) {
    console.warn("SSE not supported:", exc);
  }
}


// Auto-boot when the page mounts — admin_backup.html includes this
// module via <script type=module>.
if (typeof window !== "undefined"
    && /^https?:$/.test(window.location.protocol)
    && document.getElementById("bk-status-host")) {
  boot();
}
