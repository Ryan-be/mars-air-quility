/**
 * MLSS hub card — central node on the topology graph (Phase 6 Task 6.3).
 *
 * Port of `docs/assets/effector-map-handoff/nodes.jsx::HubCard`. The
 * hub is the only node that aggregates whole-room sensors (temp, RH,
 * CO₂); per the spec it sits at the centre of the radial layout and
 * connects directly to every grow + every hub-scoped effector.
 *
 * Structure:
 *
 *   <div class="tp-card tp-card-hub">
 *     <span class="tp-card-stripe"></span>      ← left-edge type colour
 *     <div class="tp-card-head">
 *       <div class="tp-card-title">MLSS Hub</div>
 *       <div class="tp-card-sub">central coordinator</div>
 *     </div>
 *     <div class="tp-tiles">
 *       <div class="tp-tile"><div class="tp-tile-k">Temp</div>
 *           <div class="tp-tile-v">22.5<small>°C</small></div></div>
 *       …RH, CO₂ tiles…
 *     </div>
 *     <svg class="tp-spark">…optional sparkline…</svg>
 *   </div>
 *
 * The stripe element gets a CSS variable (`--node-color`) so the
 * topology-wide stylesheet can colour every hub stripe consistently
 * without per-card inline styles.
 *
 * Missing sensor values (None from the server) render as a dash so
 * the card layout stays stable even before the first reading lands.
 */

import { renderSparkline } from "./sparkline.mjs";


const HUB_COLOUR = "var(--color-status-standby, #2dccff)";


function _fmt(value, opts = {}) {
  if (value == null || Number.isNaN(value)) return "—";
  const { decimals = 0 } = opts;
  return Number(value).toFixed(decimals);
}


function _tile(doc, key, valueHtml, unit) {
  const tile = doc.createElement("div");
  tile.className = "tp-tile";
  const k = doc.createElement("div");
  k.className = "tp-tile-k";
  k.textContent = key;
  const v = doc.createElement("div");
  v.className = "tp-tile-v";
  v.textContent = valueHtml;
  if (unit) {
    const u = doc.createElement("small");
    u.textContent = unit;
    v.appendChild(u);
  }
  tile.appendChild(k);
  tile.appendChild(v);
  return tile;
}


/**
 * Render the hub card.
 *
 * @param {object} node     One topology node (kind=hub).
 * @param {object} history  `{temp: [...]}` rolling values for the
 *                          sparkline. Empty {} when no history yet.
 * @param {Document} [doc]
 * @returns {HTMLDivElement}
 */
export function renderHubCard(node, history = {}, doc = document) {
  const card = doc.createElement("div");
  card.className = "tp-card tp-card-hub";
  card.style.setProperty("--node-color", HUB_COLOUR);

  // Left-edge stripe — type indicator that the topology CSS paints
  // via the local --node-color custom property.
  const stripe = doc.createElement("span");
  stripe.className = "tp-card-stripe";
  card.appendChild(stripe);

  // Header — title + sub-label.
  const head = doc.createElement("div");
  head.className = "tp-card-head";
  const title = doc.createElement("div");
  title.className = "tp-card-title";
  title.textContent = node.label || "MLSS Hub";
  head.appendChild(title);
  if (node.sub) {
    const sub = doc.createElement("div");
    sub.className = "tp-card-sub";
    sub.textContent = node.sub;
    head.appendChild(sub);
  }
  card.appendChild(head);

  // Three telemetry tiles. Sensor names mirror the topology endpoint
  // payload (sensors.temp / .rh / .co2).
  const tiles = doc.createElement("div");
  tiles.className = "tp-tiles";
  const sensors = node.sensors || {};
  tiles.appendChild(_tile(doc, "Temp", _fmt(sensors.temp, { decimals: 1 }), "°C"));
  tiles.appendChild(_tile(doc, "RH",   _fmt(sensors.rh,   { decimals: 0 }), "%"));
  tiles.appendChild(_tile(doc, "CO₂",  _fmt(sensors.co2,  { decimals: 0 }), "ppm"));
  card.appendChild(tiles);

  // Optional sparkline of recent temperature readings. Phase 10 SSE
  // wiring populates `history.temp` from the live event stream.
  if (history && Array.isArray(history.temp) && history.temp.length >= 2) {
    const spark = renderSparkline({
      values: history.temp,
      color: HUB_COLOUR,
      height: 24,
      ownerDocument: doc,
    });
    card.appendChild(spark);
  }
  return card;
}
