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


// Time-axis tick positions (UTC hours, evenly spaced) and their labels.
// Design-critique #10: the schedule bar previously had no time markers —
// just a coloured bar with a green "NOW" tick — so operators couldn't
// tell where in the day the green tick was. Six ticks at 4h spacing
// gives enough resolution without clutter.
const SCHEDULE_TICK_HOURS = [0, 4, 8, 12, 16, 20];


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

  // Tick marks above the track — small vertical lines + HH:00 labels
  // so the bar reads as a 24-hour clock face rather than abstract slab.
  for (const h of SCHEDULE_TICK_HOURS) {
    const tick = doc.createElement("div");
    tick.className = "du-schedule-tick";
    tick.style.left = `${(h / 24) * 100}%`;
    track.appendChild(tick);
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

  // Labels row below the track: "00:00  04:00  08:00  12:00  16:00  20:00".
  // Outside the track so they don't overlap with the on-window highlights
  // or the NOW marker.
  const labels = doc.createElement("div");
  labels.className = "du-schedule-labels";
  labels.dataset.testid = "schedule-labels";
  for (const h of SCHEDULE_TICK_HOURS) {
    const span = doc.createElement("span");
    span.className = "du-schedule-label";
    span.style.left = `${(h / 24) * 100}%`;
    span.textContent = String(h).padStart(2, "0") + ":00";
    labels.appendChild(span);
  }
  wrap.appendChild(labels);

  return wrap;
}
