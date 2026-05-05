/**
 * Render one grow unit as a card on the fleet view. Card structure:
 *   header (name + phase/medium meta + status pill)
 *   photo (latest captured or placeholder)
 *   stat tiles (capability-driven; just moisture + light + watered for now)
 *   footer (last seen + Identify + Open buttons)
 */
import { renderStatusPill } from "./status-pill.mjs";


export function renderGrowCard(unit, doc = document) {
  const card = doc.createElement("div");
  card.className = `gu-card ${unit.status}`;
  card.dataset.unitId = unit.id;

  // Header
  const head = doc.createElement("div");
  head.className = "gu-head";
  const titleBlock = doc.createElement("div");
  const name = doc.createElement("div");
  name.className = "gu-name";
  name.textContent = unit.label;
  const meta = doc.createElement("div");
  meta.className = "gu-meta";
  const dayCount = unit.sown_at
    ? Math.floor((Date.now() - new Date(unit.sown_at).getTime()) / 86400000)
    : null;
  meta.textContent = [
    unit.current_phase,
    dayCount !== null ? `day ${dayCount}` : null,
    unit.medium_type,
  ].filter(Boolean).join(" · ");
  titleBlock.appendChild(name);
  titleBlock.appendChild(meta);
  head.appendChild(titleBlock);
  head.appendChild(renderStatusPill(unit.status, { ownerDocument: doc }));
  card.appendChild(head);

  // Photo
  const photo = doc.createElement("div");
  photo.className = "gu-photo";
  const photoUrl = unit.last_known_state?.last_photo_url || null;
  if (photoUrl) {
    photo.style.backgroundImage = `url(${photoUrl})`;
  } else {
    photo.classList.add("no-photo");
    photo.textContent = "— No photo yet —";
  }
  card.appendChild(photo);

  // Stats
  const stats = doc.createElement("div");
  stats.className = "gu-stats";
  const last = unit.last_known_state || {};
  const moisture = last.soil_moisture_pct != null
    ? `${Math.round(last.soil_moisture_pct)}%` : "—";
  const lightOn = last.light_state ? "💡 ON" : "💡 OFF";
  for (const [v, l] of [
    [moisture, "Moisture"],
    [lightOn, "Light"],
    [unit.status === "online" ? "Live" : unit.status, "State"],
  ]) {
    const stat = doc.createElement("div");
    stat.className = "gu-stat";
    const vd = doc.createElement("div");
    vd.className = "v"; vd.textContent = v;
    const ld = doc.createElement("div");
    ld.className = "l"; ld.textContent = l;
    stat.appendChild(vd); stat.appendChild(ld);
    stats.appendChild(stat);
  }
  card.appendChild(stats);

  // Footer
  const foot = doc.createElement("div");
  foot.className = "gu-foot";
  const seen = doc.createElement("span");
  seen.className = "gu-lastseen";
  seen.textContent = unit.last_seen_at
    ? `Seen ${_relativeTime(new Date(unit.last_seen_at))} ago` : "Never seen";
  const actions = doc.createElement("div");
  actions.className = "gu-actions";
  const identifyBtn = doc.createElement("button");
  identifyBtn.className = "gu-btn";
  identifyBtn.dataset.action = "identify";
  identifyBtn.dataset.unitId = unit.id;
  identifyBtn.textContent = "Identify";
  const openBtn = doc.createElement("a");
  openBtn.className = "gu-btn";
  openBtn.dataset.action = "open";
  openBtn.dataset.href = `/grow/${unit.id}`;
  openBtn.href = `/grow/${unit.id}`;
  openBtn.textContent = "Open →";
  actions.appendChild(identifyBtn);
  actions.appendChild(openBtn);
  foot.appendChild(seen);
  foot.appendChild(actions);
  card.appendChild(foot);

  return card;
}


function _relativeTime(then) {
  const sec = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}
