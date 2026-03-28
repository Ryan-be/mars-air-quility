export async function setFanControl(state) {
  try {
    const res = await fetch(`/api/fan?state=${state}`, { method: "POST" });
    const result = await res.json();
    if (res.ok) {
      document.getElementById("fan-status").textContent = result.message;
      document.getElementById("fan-mode").textContent = state === "auto" ? "Auto" : "Manual";
    } else {
      document.getElementById("fan-status").textContent = result.error || "Error";
    }
  } catch {
    document.getElementById("fan-status").textContent = "Unreachable";
  }
}

export async function fetchFanStatus() {
  try {
    const res = await fetch('/api/fan/status');
    const result = await res.json();
    if (res.ok) {
      document.getElementById("fan-status").textContent = result.state;
      document.getElementById("fan-mode").textContent = result.mode ?? "--";
      document.getElementById("fan-power").textContent =
        result.power_w != null ? `${result.power_w.toFixed(1)} W` : "N/A";
      document.getElementById("fan-today").textContent =
        result.today_kwh != null ? `${result.today_kwh.toFixed(3)} kWh` : "N/A";
      const costEl = document.getElementById("fan-cost");
      if (result.today_kwh != null && result.unit_rate_pence != null) {
        const pence = result.today_kwh * result.unit_rate_pence;
        costEl.textContent = pence < 100 ? `${pence.toFixed(1)}p` : `£${(pence / 100).toFixed(2)}`;
      } else if (result.unit_rate_pence == null) {
        costEl.textContent = "set rate in settings";
      } else {
        costEl.textContent = "N/A";
      }
    }
  } catch { /* silent */ }
}

export async function fetchAutoStatus() {
  try {
    const res = await fetch('/api/fan/auto-status');
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}
