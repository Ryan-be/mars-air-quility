/**
 * 24h horizontal schedule bar with on-window highlights + NOW indicator.
 * Pure function computeOnSegments separates time math from DOM construction.
 */

function _hhMmToHours(s) {
  const [h, m] = s.split(":").map(Number);
  return h + m / 60;
}


export function computeOnSegments(windows) {
  const segs = [];
  for (const w of windows) {
    const start = _hhMmToHours(w.start);
    const end = _hhMmToHours(w.end);
    if (start <= end) {
      segs.push({ leftPct: start / 24, widthPct: (end - start) / 24 });
    } else {
      // Overnight: split into two segments
      segs.push({ leftPct: start / 24, widthPct: (24 - start) / 24 });
      segs.push({ leftPct: 0, widthPct: end / 24 });
    }
  }
  return segs;
}


export function renderScheduleBar(windows, now = new Date(), doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-schedule-bar";
  const track = doc.createElement("div");
  track.className = "du-schedule-track";

  for (const seg of computeOnSegments(windows)) {
    const onSeg = doc.createElement("div");
    onSeg.className = "du-schedule-on";
    onSeg.style.left = `${seg.leftPct * 100}%`;
    onSeg.style.width = `${seg.widthPct * 100}%`;
    track.appendChild(onSeg);
  }

  const nowMarker = doc.createElement("div");
  nowMarker.className = "du-schedule-now";
  const fracOfDay = (now.getUTCHours() + now.getUTCMinutes() / 60) / 24;
  nowMarker.style.left = `${fracOfDay * 100}%`;
  const lbl = doc.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = `NOW · ${now.toISOString().substring(11, 16)}`;
  nowMarker.appendChild(lbl);
  track.appendChild(nowMarker);

  wrap.appendChild(track);
  return wrap;
}
