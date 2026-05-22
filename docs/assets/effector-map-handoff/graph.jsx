// SVG graph layer: edges between nodes + node drag handling + pan/zoom.
// Nodes are rendered as absolutely-positioned HTML cards in a sibling layer so
// they get crisp HTML controls (buttons, focus, etc).

const NODE_CARD_W = 200; // approximate — only used for edge anchor offsets
const NODE_CARD_H = 100;

// Compute a path string between two points for a given edge style.
function edgePath(a, b, style) {
  if (style === 'ortho') {
    // L-shaped: horizontal first, then vertical.
    const midX = (a.x + b.x) / 2;
    return `M${a.x} ${a.y} L${midX} ${a.y} L${midX} ${b.y} L${b.x} ${b.y}`;
  }
  if (style === 'bezier') {
    // Smooth curve, bias toward midpoint horizontally
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy);
    const handle = Math.min(160, Math.max(40, len * 0.4));
    return `M${a.x} ${a.y} C${a.x + handle} ${a.y} ${b.x - handle} ${b.y} ${b.x} ${b.y}`;
  }
  // straight
  return `M${a.x} ${a.y} L${b.x} ${b.y}`;
}

// Edge color based on association semantics
function edgeColorFor(parent, child) {
  // Hub → grow: white-ish neutral
  if (parent.kind === 'hub' && child.kind === 'grow') return 'var(--status-standby)';
  // Effector edge — color tracks state of the effector
  if (child.kind === 'effector') {
    if (child.state === 'on') return 'var(--status-nominal)';
    if (child.mode === 'auto') return 'var(--status-standby)';
    return 'var(--status-off)';
  }
  return 'var(--text-tertiary)';
}

// Compute an edge anchor — the point on a node's bounding box facing the
// other endpoint. We use a small inset so the line meets the card edge cleanly.
function anchorOn(node, towards, halfW = NODE_CARD_W / 2, halfH = NODE_CARD_H / 2) {
  const dx = towards.x - node.x;
  const dy = towards.y - node.y;
  if (dx === 0 && dy === 0) return { x: node.x, y: node.y };
  // intersect ray from node center with rectangle
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const slope = absY / absX;
  if (slope < halfH / halfW) {
    // exits through left/right
    const sx = dx > 0 ? 1 : -1;
    return { x: node.x + sx * halfW, y: node.y + sx * halfW * (dy / dx) };
  }
  const sy = dy > 0 ? 1 : -1;
  return { x: node.x + sy * halfH * (dx / dy), y: node.y + sy * halfH };
}

function Edges({ nodes, positions, edgeStyle, animate }) {
  const byId = React.useMemo(
    () => Object.fromEntries(nodes.map((n) => [n.id, n])),
    [nodes],
  );
  const edges = [];
  nodes.forEach((n) => {
    if (!n.parent) return;
    const p = byId[n.parent];
    if (!p) return;
    const a = positions[p.id];
    const b = positions[n.id];
    if (!a || !b) return;
    edges.push({ id: `${p.id}__${n.id}`, p, c: n, a, b });
  });

  return (
    <>
      {edges.map(({ id, p, c, a, b }) => {
        const color = edgeColorFor(p, c);
        // Anchor lines to card edges
        const sizeP = p.kind === 'hub' ? { w: 240, h: 130 } : { w: 200, h: 100 };
        const sizeC = c.kind === 'effector' ? { w: 200, h: 110 } : { w: 220, h: 130 };
        const anchorA = anchorOn(a, b, sizeP.w / 2, sizeP.h / 2);
        const anchorB = anchorOn(b, a, sizeC.w / 2, sizeC.h / 2);
        const isFlowing = animate && c.kind === 'effector' && c.state === 'on';
        const d = edgePath(anchorA, anchorB, edgeStyle);

        return (
          <g key={id} style={{ color }}>
            {/* Base wire — slightly thicker, dimmed */}
            <path className="edge edge-base" d={d}
                  stroke={color} strokeWidth={1.5} />
            {/* Flow overlay — only if effector is on */}
            {isFlowing && (
              <path className="edge edge-flow" d={d}
                    stroke={color} strokeWidth={1.6} />
            )}
          </g>
        );
      })}
    </>
  );
}

Object.assign(window, { Edges, edgePath, anchorOn, edgeColorFor });
