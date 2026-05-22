/**
 * Grow-unit card on the topology graph (Phase 6 Task 6.4).
 *
 * Filename `topology-grow-card.mjs` to disambiguate from the existing
 * `static/js/grow/components/grow-card.mjs` — that one is the fleet
 * view's card on /grow (with photo, larger footprint, identify button)
 * whereas this one is a smaller dense card for the node-map view.
 *
 * Renders the grow unit's chrome:
 *
 *   <div class="tp-card tp-card-grow">
 *     <span class="tp-card-stripe"></span>
 *     <div class="tp-card-head">
 *       <div class="tp-card-title">Grow #1</div>
 *       <div class="tp-card-sub">tomato</div>
 *       <span class="tp-chip-phase">vegetative</span>
 *     </div>
 *     <div class="tp-tiles">       ← soil moisture / soil temp / air temp
 *       …three .tp-tile divs…
 *     </div>
 *     <svg class="tp-spark">…optional soil-moisture trend…</svg>
 *   </div>
 *
 * Sensor names mirror the topology endpoint payload
 * (`sensors.soil_moisture`, `.soil_temp_c`, `.air_temp_c`). Soil
 * moisture renders as the sparkline (rather than temp) because it's
 * the most operationally interesting curve on a grow unit — a
 * downward trend means watering soon.
 */

import { renderSparkline } from "./sparkline.mjs";


const GROW_COLOUR = "var(--color-status-normal, #56f000)";


function _fmt(value, opts = {}) {
  if (value == null || Number.isNaN(value)) return "—";
  const { decimals = 0 } = opts;
  return Number(value).toFixed(decimals);
}


function _tile(doc, key, value, unit) {
  const tile = doc.createElement("div");
  tile.className = "tp-tile";
  const k = doc.createElement("div");
  k.className = "tp-tile-k";
  k.textContent = key;
  const v = doc.createElement("div");
  v.className = "tp-tile-v";
  v.textContent = value;
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
 * Render the grow card.
 *
 * @param {object} node     One topology node (kind=grow).
 * @param {object} history  `{soil_moisture: [...]}` rolling window.
 * @param {Document} [doc]
 * @returns {HTMLDivElement}
 */
export function renderTopologyGrowCard(node, history = {}, doc = document) {
  const card = doc.createElement("div");
  card.className = "tp-card tp-card-grow";
  card.style.setProperty("--node-color", GROW_COLOUR);

  // Left-edge type stripe.
  const stripe = doc.createElement("span");
  stripe.className = "tp-card-stripe";
  card.appendChild(stripe);

  // Header: title + plant type + phase chip.
  const head = doc.createElement("div");
  head.className = "tp-card-head";

  const title = doc.createElement("div");
  title.className = "tp-card-title";
  title.textContent = node.label || `Grow ${node.id}`;
  head.appendChild(title);

  if (node.plant_type) {
    const sub = doc.createElement("div");
    sub.className = "tp-card-sub";
    sub.textContent = node.plant_type;
    head.appendChild(sub);
  }

  if (node.phase) {
    const chip = doc.createElement("span");
    chip.className = "tp-chip-phase";
    chip.textContent = node.phase;
    head.appendChild(chip);
  }

  card.appendChild(head);

  // Three telemetry tiles. Sensors keyed to the topology endpoint
  // names — None values surface as the em-dash placeholder.
  const tiles = doc.createElement("div");
  tiles.className = "tp-tiles";
  const sensors = node.sensors || {};
  tiles.appendChild(
    _tile(doc, "Soil", _fmt(sensors.soil_moisture, { decimals: 0 }), "%"),
  );
  tiles.appendChild(
    _tile(doc, "Soil °C", _fmt(sensors.soil_temp_c, { decimals: 1 }), "°C"),
  );
  tiles.appendChild(
    _tile(doc, "Air", _fmt(sensors.air_temp_c, { decimals: 1 }), "°C"),
  );
  card.appendChild(tiles);

  // Sparkline of recent soil-moisture readings — the most useful
  // trend for a grow unit (downward = watering due soon).
  if (history && Array.isArray(history.soil_moisture) && history.soil_moisture.length >= 2) {
    const spark = renderSparkline({
      values: history.soil_moisture,
      color: GROW_COLOUR,
      height: 24,
      ownerDocument: doc,
    });
    card.appendChild(spark);
  }

  return card;
}
