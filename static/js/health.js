function setHealthStatus(id, value) {
  const el = document.getElementById(id);
  el.textContent = value;
  el.className = "h-value " + (value === "OK" ? "ok" : "error");
}

export function applyHealth(stat) {
  setHealthStatus("aht20Status", stat.AHT20);
  setHealthStatus("sgp30Status", stat.SGP30);
  setHealthStatus("pmStatus",    stat.PM_sensor);
  setHealthStatus("mics6814Status", stat.MICS6814);
  setHealthStatus("plugStatus",  stat.smart_plug);

  document.getElementById("cpuUsage").textContent      = stat.cpu_usage;
  document.getElementById("memoryUsage").textContent   = `${stat.memory_used} / ${stat.memory_total} (${stat.memory_percent})`;
  document.getElementById("diskUsage").textContent     = `${stat.disk_used} / ${stat.disk_total} (${stat.disk_percent})`;
  document.getElementById("dbSize").textContent        = stat.db_size;
  document.getElementById("uptime").textContent        = stat.uptime;
  document.getElementById("serviceUptime").textContent = stat.service_uptime;
}

export async function fetchHealth() {
  try {
    const res  = await fetch('/system_health');
    applyHealth(await res.json());
  } catch {
    ["aht20Status", "sgp30Status", "pmStatus", "mics6814Status", "plugStatus"].forEach(id => {
      const el = document.getElementById(id);
      el.textContent = "Unavailable";
      el.className = "h-value error";
    });
  }
}
