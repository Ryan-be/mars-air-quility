/**
 * Tiny SVG line chart used inside hub + grow cards (Phase 6 Task 6.1).
 *
 * Port of `docs/assets/effector-map-handoff/nodes.jsx::Sparkline`. The
 * implementation is deliberately the smallest thing that draws a
 * recognisable trend line:
 *
 *   * One `<polyline>` with `n` points.
 *   * x maps linearly across `[0, 100]` (the SVG's viewBox width).
 *   * y maps linearly within the value range, with 2px padding top
 *     + bottom so the extreme readings don't touch the box edge.
 *   * `preserveAspectRatio="none"` so the SVG stretches to whatever
 *     container width the card gives it.
 *
 * Series with fewer than two values can't form a line — the renderer
 * still returns a node (an empty `<svg>`) so the caller can `.append`
 * unconditionally without a null guard.
 */

const SVG_NS = "http://www.w3.org/2000/svg";


/**
 * @param {object} args
 * @param {number[]} args.values Numeric series, oldest → newest.
 * @param {string} [args.color] Stroke colour (any CSS colour value).
 * @param {number} [args.height=24] SVG height in pixels.
 * @param {Document} [args.ownerDocument]
 * @returns {SVGElement}
 */
export function renderSparkline({ values, color, height = 24, ownerDocument }) {
  const doc = ownerDocument || document;
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "tp-spark");
  const w = 100;
  const h = height;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.style.height = `${h}px`;
  if (color) svg.style.setProperty("--node-color", color);

  // Fewer than 2 values → no line possible. Return the bare SVG so
  // the caller can still mount it (height is preserved so layout
  // doesn't shift when the first reading lands).
  if (!values || values.length < 2) return svg;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(0.001, max - min);
  const stepX = w / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = i * stepX;
    // y inverted (SVG y-down) + 2px padding top/bottom so the line
    // doesn't crowd the viewBox edge on the extreme values.
    const y = h - 2 - ((v - min) / range) * (h - 4);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const pl = doc.createElementNS(SVG_NS, "polyline");
  pl.setAttribute("points", pts.join(" "));
  pl.setAttribute("class", "tp-spark-line");
  if (color) pl.setAttribute("stroke", color);
  pl.setAttribute("fill", "none");
  pl.setAttribute("stroke-width", "1.2");
  svg.appendChild(pl);
  return svg;
}
