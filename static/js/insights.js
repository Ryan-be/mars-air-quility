// ── Derived atmospheric calculations ─────────────────────────────────────────

export function dewPoint(tempC, rh) {
  // Magnus formula
  const a = 17.625, b = 243.04;
  const alpha = Math.log(rh / 100) + a * tempC / (b + tempC);
  return (b * alpha) / (a - alpha);
}

export function feelsLike(tempC, rh) {
  // Australian BOM apparent temperature (indoors, no wind)
  const e = (rh / 100) * 6.105 * Math.exp(17.27 * tempC / (237.7 + tempC));
  return tempC + 0.33 * e - 4.00;
}

export function vpdKpa(tempC, rh) {
  // Tetens formula for saturation vapour pressure (kPa)
  const svp = 0.6108 * Math.exp(17.27 * tempC / (tempC + 237.3));
  return svp * (1 - rh / 100);
}

export function timeToThreshold(values, thresholdPpm, intervalSecs) {
  // Linear extrapolation from last 6 readings
  const n = Math.min(values.length, 6);
  if (n < 2) return null;
  const recent = values.slice(-n);
  const rate = (recent[recent.length - 1] - recent[0]) / (n - 1); // ppm per interval
  if (rate <= 0) return null; // not rising
  const current = recent[recent.length - 1];
  if (current >= thresholdPpm) return 0;
  const intervalsLeft = (thresholdPpm - current) / rate;
  return Math.round(intervalsLeft * intervalSecs / 60); // minutes
}

export function airQuality(tvoc, eco2) {
  if (tvoc <= 250 && eco2 <= 800)  return { label: "Good",     cls: "good",     border: "#2d8a2d" };
  if (tvoc <= 500 && eco2 <= 1500) return { label: "Moderate", cls: "moderate", border: "#c87800" };
  return                                  { label: "Poor",     cls: "poor",     border: "#b03030" };
}

export function co2Alert(eco2) {
  if (eco2 <= 800)  return { label: "Normal",    cls: "good",     sub: "Safe for cognitive function" };
  if (eco2 <= 1000) return { label: "Elevated",  cls: "moderate", sub: "Monitor closely" };
  if (eco2 <= 2000) return { label: "Impaired",  cls: "poor",     sub: "Cognitive impact likely" };
  return                   { label: "Dangerous", cls: "danger",   sub: "Evacuate or ventilate now" };
}

export function updateInsights(temp, hum, tvoc, eco2, eco2Series) {
  // Air quality
  const aq = airQuality(tvoc, eco2);
  const aqEl = document.getElementById("aqValue");
  aqEl.textContent = aq.label;
  aqEl.className = `value ${aq.cls}`;
  document.getElementById("aqCard").style.borderTopColor = aq.border;
  document.getElementById("aqSub").textContent = `TVOC ${tvoc} ppb · eCO₂ ${eco2} ppm`;

  // Dew point
  const dp = dewPoint(temp, hum);
  document.getElementById("dewValue").textContent = `${dp.toFixed(1)} °C`;
  const dpDiff = temp - dp;
  document.getElementById("dewSub").textContent =
    dpDiff < 3 ? "⚠️ Condensation risk" : `${dpDiff.toFixed(1)} °C margin to condensation`;

  // Feels like
  const fl = feelsLike(temp, hum);
  document.getElementById("hiValue").textContent = `${fl.toFixed(1)} °C`;

  // CO2 alert
  const co2 = co2Alert(eco2);
  const co2El = document.getElementById("co2AlertValue");
  co2El.textContent = co2.label;
  co2El.className = `value ${co2.cls}`;
  document.getElementById("co2Card").style.borderTopColor =
    co2.cls === "good" ? "#2d8a2d" : co2.cls === "moderate" ? "#c87800" : "#b03030";
  document.getElementById("co2AlertSub").textContent = co2.sub;

  // VPD
  const vpd = vpdKpa(temp, hum);
  const vpdEl = document.getElementById("vpdValue");
  vpdEl.textContent = `${vpd.toFixed(2)} kPa`;
  let vpdCls = "neutral", vpdSub = "";
  if      (vpd < 0.4)  { vpdCls = "moderate"; vpdSub = "Too humid — mould risk"; }
  else if (vpd <= 0.8) { vpdCls = "good";     vpdSub = "Good — seedlings/clones"; }
  else if (vpd <= 1.2) { vpdCls = "good";     vpdSub = "Ideal — vegetative growth"; }
  else if (vpd <= 1.6) { vpdCls = "moderate"; vpdSub = "High — increase humidity"; }
  else                 { vpdCls = "poor";     vpdSub = "Too dry — plant stress"; }
  vpdEl.className = `value ${vpdCls}`;
  document.getElementById("vpdSub").textContent = vpdSub;

  // Time to CO₂ threshold (1000 ppm = cognitive impairment level)
  const LOG_INTERVAL_S = 10;
  const mins = timeToThreshold(eco2Series, 1000, LOG_INTERVAL_S);
  const tttEl = document.getElementById("tttValue");
  if (mins === null)  { tttEl.textContent = "Stable";    tttEl.className = "value good"; }
  else if (mins === 0){ tttEl.textContent = "Now";       tttEl.className = "value danger"; }
  else if (mins < 10) { tttEl.textContent = `~${mins} min`; tttEl.className = "value poor"; }
  else if (mins < 30) { tttEl.textContent = `~${mins} min`; tttEl.className = "value moderate"; }
  else                { tttEl.textContent = `~${mins} min`; tttEl.className = "value neutral"; }
}
