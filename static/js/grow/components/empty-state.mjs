/**
 * Guided onboarding panel shown when zero units are enrolled.
 * Numbered steps, copy-button enrollment key, install one-liner.
 */

export function renderEmptyState({ enrollmentKey, mlssHost }, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "empty-wrap";

  const card = doc.createElement("div");
  card.className = "empty-card";

  card.innerHTML = `
    <div class="hero">
      <div class="icon">🌱</div>
      <div>
        <h3>No grow units enrolled yet</h3>
        <p class="sub">Get a Pi Zero W with the Automation pHAT online in about 5 minutes.</p>
      </div>
    </div>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-body">
          <strong>Copy your household enrollment key.</strong>
          ${enrollmentKey
            ? `<div class="key-display"><code>${enrollmentKey}</code><button class="copy-btn" data-copy="${enrollmentKey}">📋 Copy</button></div>`
            : `<p style="color:#ffb302">Already revealed — go to Settings → Grow to rotate (Phase 2).</p>`}
        </div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-body"><strong>Flash Raspberry Pi OS Lite</strong> with WiFi + SSH preconfigured.</div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-body">
          <strong>Drop /boot/mlss-grow.yaml</strong> on the SD card before ejecting:
          <pre><code>mlss_host: ${mlssHost}
enrollment_key: ${enrollmentKey || '<your-key>'}
plant:
  name: Tomato 1</code></pre>
        </div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div class="step-body">
          <strong>Insert + power on; SSH in once and run:</strong>
          <pre><code>curl -k https://${mlssHost}:5000/api/grow/install.sh | sudo bash</code></pre>
        </div>
      </div>
      <div class="step">
        <div class="step-num">5</div>
        <div class="step-body"><strong>Done.</strong> Unit appears in this Grow tab within ~60 seconds.</div>
      </div>
    </div>
    <div class="empty-foot">
      <a href="/static/docs/PLANT_GROW_UNIT_SETUP.md" class="doc-link">📖 Full setup guide →</a>
    </div>
  `;

  // Wire copy button
  card.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".copy-btn");
    if (!btn) return;
    navigator.clipboard?.writeText(btn.dataset.copy);
    btn.textContent = "✓ Copied";
    setTimeout(() => { btn.textContent = "📋 Copy"; }, 2000);
  });

  wrap.appendChild(card);
  return wrap;
}
