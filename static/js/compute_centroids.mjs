// Pure centroid math for the incidents graph. Kept as an ES module so it
// can be fixture-tested in Node without a DOM.
//
// Three view modes share the same timeline-in-hull geometry; only the
// packing/sizing constants differ:
//   manual        — default, roomy, matches the hull padding in CSS
//   compact       — denser for "scan many incidents" use
//   chronological — single row, clusters ordered by started_at ascending
//
// Contract: given a list of incidents with { id, alert_count, primary_count,
// started_at? } and a view mode, return { [id]: {x, y}, __crossBandY }.

const MODES = {
  manual: {
    MIN_WIDTH_PX:       360,
    PX_PER_ALERT:       32,
    HULL_PADDING_PX:    80,
    INTER_CLUSTER_GAP:  70,
    LANE_HEIGHT_PX:     44,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,
  },
  compact: {
    MIN_WIDTH_PX:       240,
    PX_PER_ALERT:       20,
    HULL_PADDING_PX:    60,
    INTER_CLUSTER_GAP:  40,
    LANE_HEIGHT_PX:     32,
    STACK_DY_PX:        14,
    INTER_ROW_GAP:      40,
  },
  chronological: {
    MIN_WIDTH_PX:       300,
    PX_PER_ALERT:       28,
    HULL_PADDING_PX:    70,
    INTER_CLUSTER_GAP:  50,
    LANE_HEIGHT_PX:     40,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,  // unused in single-row mode
  },
};

// Max STACK_STEPS depth (from incident_graph.js STACK_STEPS array = 11 slots
// = 5 steps each side). A cluster's stack depth is bounded by how many
// alerts share a severity lane; with 3 lanes and even distribution, the
// worst-case slot used ≈ ceil(primary_count / 3), capped at 5.
const MAX_STACK_STEPS = 5;

function clusterHalfHeight(primaryCount, c) {
  const primary = Math.max(1, primaryCount || 0);
  const stackSlots = Math.min(MAX_STACK_STEPS, Math.ceil(primary / 3));
  return c.LANE_HEIGHT_PX + stackSlots * c.STACK_DY_PX;
}

function clusterWidth(alertCount, c) {
  const count = Math.max(1, alertCount || 0);
  return Math.max(c.MIN_WIDTH_PX, count * c.PX_PER_ALERT) + 2 * c.HULL_PADDING_PX;
}

export function computeCentroids(incidents, viewMode = 'manual') {
  const c = MODES[viewMode] || MODES.manual;
  const n = incidents.length;
  if (n === 0) return { __crossBandY: 0 };

  // Chronological: single row, sorted by started_at ascending.
  if (viewMode === 'chronological') {
    // started_at is an ISO-8601 string from the API; localeCompare on ISO
    // strings is equivalent to chronological order for same-timezone values.
    const sorted = [...incidents].sort(
      (a, b) => String(a.started_at || '').localeCompare(String(b.started_at || ''))
    );
    const centroids = {};
    let cursor = 0;
    let maxHalfH = 0;
    for (const inc of sorted) {
      const w = clusterWidth(inc.alert_count, c);
      const halfH = clusterHalfHeight(inc.primary_count, c);
      maxHalfH = Math.max(maxHalfH, halfH);
      centroids[inc.id] = { x: cursor + w / 2, y: 0 };
      cursor += w + c.INTER_CLUSTER_GAP;
    }
    centroids.__crossBandY = maxHalfH + 140;
    return centroids;
  }

  // Grid modes: sqrt(n) columns, dynamic per-row height from max stack depth.
  const cols = Math.ceil(Math.sqrt(Math.max(n, 1)));
  const widths  = incidents.map(i => clusterWidth(i.alert_count, c));
  const halfHs  = incidents.map(i => clusterHalfHeight(i.primary_count, c));

  // Row-wise max half-height.
  const rows = Math.ceil(n / cols);
  const rowHalfH = [];
  for (let r = 0; r < rows; r++) {
    let m = 0;
    for (let cc = 0; cc < cols && r * cols + cc < n; cc++) {
      m = Math.max(m, halfHs[r * cols + cc]);
    }
    rowHalfH.push(m);
  }

  // Row centre Y: row 0 at y=0; subsequent rows at prev_centre + prev_half_h +
  // this_half_h + INTER_ROW_GAP.
  const rowCentreY = [0];
  for (let r = 1; r < rows; r++) {
    rowCentreY.push(
      rowCentreY[r - 1] + rowHalfH[r - 1] + rowHalfH[r] + c.INTER_ROW_GAP
    );
  }

  const centroids = {};
  for (let r = 0; r < rows; r++) {
    let cursor = 0;
    for (let cc = 0; cc < cols && r * cols + cc < n; cc++) {
      const idx = r * cols + cc;
      centroids[incidents[idx].id] = {
        x: cursor + widths[idx] / 2,
        y: rowCentreY[r],
      };
      cursor += widths[idx] + c.INTER_CLUSTER_GAP;
    }
  }

  centroids.__crossBandY =
    rowCentreY[rows - 1] + rowHalfH[rows - 1] + 140;
  return centroids;
}
