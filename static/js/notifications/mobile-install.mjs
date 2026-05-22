/**
 * Renders the Mobile install card body — TLS status line, download
 * buttons for the CA cert + iOS .mobileconfig profile, collapsible
 * install instructions.
 */


export function renderMobileInstallCard(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const card = doc.createElement("div");
  card.className = "card mobile-install-card";
  card.dataset.testid = "mobile-install-card";

  card.innerHTML = `
    <h3>📱 Mobile install (iOS)</h3>
    <p class="card-desc">
      Install MLSS as an app on your iPhone. First time only: your device
      needs to trust the MLSS root CA (one-time profile install).
    </p>
    <p class="mobile-install-status" data-testid="mi-status">Loading TLS status…</p>

    <div class="mobile-install-actions">
      <a href="/api/admin/tls/ios-profile.mobileconfig"
         class="btn-save" data-testid="mi-profile-link"
         download="mlss-mobile.mobileconfig">
        ⬇ Download iOS Profile
      </a>
      <a href="/api/admin/tls/ca.crt"
         class="btn-secondary" data-testid="mi-ca-link"
         download="mlss-root-ca.crt">
        ⬇ Download CA cert
      </a>
      <button type="button" class="btn-secondary"
              data-testid="mi-instructions-btn">
        View install instructions
      </button>
    </div>

    <div class="mobile-install-instructions" data-testid="mi-instructions"
         style="display:none">
      <ol>
        <li>On your iPhone, open Safari and visit
          <code>https://&lt;hub&gt;/admin</code>.</li>
        <li>Tap "Download iOS Profile" above (the .mobileconfig file).</li>
        <li>Open the iOS Settings app — you'll see "Profile Downloaded"
          at the top. Tap it.</li>
        <li>Tap "Install" (top-right). Enter your device passcode if
          prompted.</li>
        <li>Settings → General → About → Certificate Trust Settings.
          Toggle ON for "MLSS Root CA".</li>
        <li>Back in Safari, visit <code>https://&lt;hub&gt;/</code>. The
          padlock should now be green.</li>
        <li>Share → Add to Home Screen. The MLSS icon will appear on
          your home screen — tap it to launch in standalone mode.</li>
        <li>Visit Settings → Notifications card in MLSS and tap "Enable
          push on this device". Grant permission when prompted.</li>
      </ol>
    </div>
  `;

  const statusEl = card.querySelector("[data-testid='mi-status']");
  const btnInstr = card.querySelector("[data-testid='mi-instructions-btn']");
  const panel    = card.querySelector("[data-testid='mi-instructions']");

  btnInstr.addEventListener("click", () => {
    panel.style.display = panel.style.display === "none" ? "block" : "none";
  });

  // Load TLS status
  (async () => {
    try {
      const r = await fetchFn("/api/admin/tls/status");
      if (!r.ok) {
        statusEl.textContent = "TLS status unavailable.";
        statusEl.className = "mobile-install-status status-err";
        return;
      }
      const d = await r.json();
      if (!d.ca_exists) {
        statusEl.innerHTML =
          "<strong>CA missing.</strong> Run <code>bash scripts/" +
          "generate_local_ca.sh</code> on the hub before installing.";
        statusEl.className = "mobile-install-status status-err";
        return;
      }
      const expires = d.cert_not_after
        ? ` · leaf cert expires ${d.cert_not_after.slice(0, 10)}`
        : "";
      statusEl.innerHTML =
        `<strong>CA present ✓</strong>${expires}`;
      statusEl.className = "mobile-install-status status-ok";
    } catch (e) {
      statusEl.textContent = "TLS status check failed.";
      statusEl.className = "mobile-install-status status-err";
    }
  })();

  return card;
}
