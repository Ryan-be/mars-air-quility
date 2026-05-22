// MLSS node-map main app.
// Wires data + layout + nodes + edges + side panel + Tweaks.

const { useState, useEffect, useRef, useCallback, useMemo } = React;

// Persisted user-tweakable design knobs
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "nodeStyle": "outline",
  "edgeStyle": "bezier",
  "density": "regular",
  "colorMode": "type",
  "icons": true,
  "background": "dots",
  "liveFeel": "medium",
  "showMinimap": false,
  "showLegend": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [nodes, setNodes] = useState(() => window.MLSS_NODES.map((n) => ({ ...n })));
  const [positions, setPositions] = useState(() => {
    const saved = window.MLSS_LAYOUT.loadPositions();
    return saved || window.MLSS_LAYOUT.autoLayout(window.MLSS_NODES);
  });
  const [viewport, setViewport] = useState({ x: 0, y: 0, k: 0.9 });
  const [selectedId, setSelectedId] = useState(null);
  const [dragId, setDragId] = useState(null);
  const [panning, setPanning] = useState(false);
  const [now, setNow] = useState(() => new Date());

  const wrapRef = useRef(null);

  // ── Center the graph on first mount ──────────────────────
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Center origin (0,0 world) at midpoint of viewport
    setViewport((v) => ({ ...v, x: r.width / 2, y: r.height / 2 - 40 }));
    // re-center on resize
    const onResize = () => {
      const rr = wrapRef.current?.getBoundingClientRect();
      if (!rr) return;
      setViewport((v) => ({ ...v, x: rr.width / 2 + (v.x - r.width / 2),
                                   y: rr.height / 2 - 40 + (v.y - (r.height / 2 - 40)) }));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Live tick: clock + sensors drift + occasional state changes ──
  const [history, setHistory] = useState(() => {
    // Seed each hub/grow with sparkline history (last 30 readings)
    const h = {};
    window.MLSS_NODES.forEach((n) => {
      if (n.sensors) {
        h[n.id] = { temp: Array.from({ length: 30 }, () => n.sensors.temp + (Math.random() - 0.5) * 1.2) };
      }
    });
    return h;
  });

  useEffect(() => {
    const id = setInterval(() => {
      setNow(new Date());
      // Drift sensor values
      setNodes((curr) => curr.map((n) => {
        if (!n.sensors) return n;
        const drift = () => (Math.random() - 0.5) * 0.2;
        const next = { ...n, sensors: { ...n.sensors } };
        if ('temp' in next.sensors) next.sensors.temp = clamp(next.sensors.temp + drift(), 18, 32);
        if ('rh'   in next.sensors) next.sensors.rh   = clamp(next.sensors.rh   + drift() * 4, 30, 90);
        if ('co2'  in next.sensors) next.sensors.co2  = clamp(next.sensors.co2  + (Math.random() - 0.5) * 30, 400, 1400);
        if ('soil' in next.sensors) next.sensors.soil = clamp(next.sensors.soil + drift() * 1.5, 20, 90);
        return next;
      }));
      setHistory((curr) => {
        const next = {};
        Object.keys(curr).forEach((id) => {
          const n = window.MLSS_NODES.find((x) => x.id === id);
          if (!n || !n.sensors) { next[id] = curr[id]; return; }
          const last = curr[id].temp[curr[id].temp.length - 1];
          const nextVal = clamp(last + (Math.random() - 0.5) * 0.6, 18, 32);
          next[id] = { temp: [...curr[id].temp.slice(-29), nextVal] };
        });
        return next;
      });
    }, 1500);
    return () => clearInterval(id);
  }, []);

  // ── Save positions when they change ────────────────────────
  useEffect(() => {
    window.MLSS_LAYOUT.savePositions(positions);
  }, [positions]);

  // ── Pan handling ───────────────────────────────────────────
  const onWrapMouseDown = useCallback((e) => {
    // Only pan on left button, ignoring drags that originated from a card
    if (e.button !== 0) return;
    if (e.target.closest('.node')) return;
    if (e.target.closest('.side, .topbar, .statusbar, .twk-panel, .zoom-widget, .legend, .minimap')) return;

    setPanning(true);
    const startX = e.clientX, startY = e.clientY;
    const startVx = viewport.x, startVy = viewport.y;
    const move = (ev) => {
      setViewport((v) => ({ ...v, x: startVx + (ev.clientX - startX),
                                   y: startVy + (ev.clientY - startY) }));
    };
    const up = () => {
      setPanning(false);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }, [viewport.x, viewport.y]);

  // ── Zoom (wheel) ───────────────────────────────────────────
  const onWheel = useCallback((e) => {
    if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > 0) {
      e.preventDefault();
      const r = wrapRef.current.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      setViewport((v) => {
        const scale = Math.exp(-e.deltaY * 0.0015);
        const newK = clamp(v.k * scale, 0.3, 2.5);
        // zoom around mouse
        const wx = (mx - v.x) / v.k;
        const wy = (my - v.y) / v.k;
        return { k: newK, x: mx - wx * newK, y: my - wy * newK };
      });
    }
  }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [onWheel]);

  // ── Node drag ──────────────────────────────────────────────
  const startNodeDrag = (id) => (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.modebtn')) return; // don't drag from mode buttons
    e.stopPropagation();
    setDragId(id);
    const startX = e.clientX, startY = e.clientY;
    const startPos = positions[id];
    let moved = false;
    const move = (ev) => {
      const dx = (ev.clientX - startX) / viewport.k;
      const dy = (ev.clientY - startY) / viewport.k;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      setPositions((p) => ({ ...p, [id]: { x: startPos.x + dx, y: startPos.y + dy } }));
    };
    const up = (ev) => {
      setDragId(null);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      // If didn't really move, treat as a click → open panel
      if (!moved) setSelectedId(id);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  // ── Effector mutations ─────────────────────────────────────
  const setMode = (id, mode) => {
    setNodes((curr) => curr.map((n) => {
      if (n.id !== id) return n;
      // Derive state from mode (auto picks based on parent sensors — mock)
      const state = mode === 'on' ? 'on' :
                    mode === 'off' ? 'off' :
                    /* auto */ deriveAutoState(n, curr);
      const power = state === 'on' ? (n.power || 50) : 0;
      return { ...n, mode, state, power: power || (state === 'on' ? 50 : 0) };
    }));
  };
  const setReparent = (id, newParent) => {
    setNodes((curr) => curr.map((n) => n.id === id ? { ...n, parent: newParent } : n));
  };
  const setPower = (id, power) => {
    setNodes((curr) => curr.map((n) => n.id === id ? { ...n, power } : n));
  };

  // ── Selected node (re-resolves on every render) ───────────
  const selected = selectedId ? nodes.find((n) => n.id === selectedId) : null;

  // ── View transform ─────────────────────────────────────────
  const tf = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.k})`;

  // ── Counters for topbar ────────────────────────────────────
  const stats = useMemo(() => {
    const total = nodes.length;
    const active = nodes.filter((n) => n.state === 'on').length;
    const effectors = nodes.filter((n) => n.kind === 'effector');
    const grows = nodes.filter((n) => n.kind === 'grow').length;
    const auto = effectors.filter((n) => n.mode === 'auto').length;
    const forced = effectors.filter((n) => n.mode !== 'auto').length;
    return { total, active, grows, effectors: effectors.length, auto, forced };
  }, [nodes]);

  // ── Animate the live-edge pulses ───────────────────────────
  const animateEdges = t.liveFeel !== 'subtle';

  // ── Density / theme classes ────────────────────────────────
  const appCls = [
    'app',
    t.density === 'compact' ? 'compact' : '',
    t.density === 'spacious' ? 'spacious' : '',
    !t.icons ? 'no-icons' : '',
    t.nodeStyle === 'solid' ? 'solid' : '',
    t.colorMode === 'status' ? 'by-status' : '',
  ].filter(Boolean).join(' ');

  const bgCls = `graph-wrap bg-${t.background}`;

  return (
    <div className={appCls}>

      {/* ─── Top bar ─── */}
      <header className="topbar">
        <div className="topbar-brand">
          <span className="mark" />
          MLSS · NODE MAP
        </div>
        <div className="topbar-cells">
          <div className="tcell">
            <span className="tcell-k">Mission Time</span>
            <span className="tcell-v">T+ {missionTime(now)}</span>
          </div>
          <div className="tcell ok">
            <span className="tcell-k">Hub</span>
            <span className="tcell-v">● NOMINAL</span>
          </div>
          <div className="tcell">
            <span className="tcell-k">Grow Units</span>
            <span className="tcell-v">{stats.grows} / {stats.grows}</span>
          </div>
          <div className="tcell">
            <span className="tcell-k">Effectors</span>
            <span className="tcell-v">{stats.effectors}</span>
          </div>
          <div className="tcell ok">
            <span className="tcell-k">Active</span>
            <span className="tcell-v">{stats.active}</span>
          </div>
          <div className="tcell">
            <span className="tcell-k">Auto / Forced</span>
            <span className="tcell-v">{stats.auto} / {stats.forced}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="tbtn" onClick={() => {
            window.MLSS_LAYOUT.resetPositions();
            setPositions(window.MLSS_LAYOUT.autoLayout(window.MLSS_NODES));
          }}>↻ Re-arrange</button>
          <button className="tbtn" onClick={() => {
            const r = wrapRef.current.getBoundingClientRect();
            setViewport({ k: 0.9, x: r.width / 2, y: r.height / 2 - 40 });
          }}>⊕ Recenter</button>
        </div>
      </header>

      {/* ─── Graph viewport ─── */}
      <div ref={wrapRef}
           className={`${bgCls}${panning ? ' panning' : ''}`}
           onMouseDown={onWrapMouseDown}>

        {/* SVG layer for edges */}
        <svg className="graph-svg" preserveAspectRatio="xMidYMid meet">
          <g style={{ transform: tf, transformOrigin: '0 0' }}>
            <Edges nodes={nodes} positions={positions}
                   edgeStyle={t.edgeStyle} animate={animateEdges} />
          </g>
        </svg>

        {/* HTML layer for node cards */}
        <div className="nodes-layer" style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
        }}>
          <div style={{
            position: 'absolute', left: 0, top: 0,
            transform: tf, transformOrigin: '0 0',
            pointerEvents: 'none',
          }}>
            {nodes.map((n) => {
              const p = positions[n.id];
              if (!p) return null;
              const isSelected = selectedId === n.id;
              const isDrag = dragId === n.id;
              return (
                <div key={n.id}
                     className={`node ${n.kind}${isSelected ? ' selected' : ''}${isDrag ? ' dragging' : ''}`}
                     data-mode={n.kind === 'effector' ? n.mode : undefined}
                     data-stage={n.kind === 'grow' ? n.stage : undefined}
                     style={{
                       left: p.x, top: p.y, pointerEvents: 'auto',
                     }}
                     onMouseDown={startNodeDrag(n.id)}>
                  <span className="reticle"><i className="a" /><i className="b" /></span>
                  {n.kind === 'hub' && <HubCard node={n} history={history[n.id] || { temp: [] }} />}
                  {n.kind === 'grow' && <GrowCard node={n} history={history[n.id] || { temp: [] }} />}
                  {n.kind === 'effector' &&
                    <EffectorCard node={n} onMode={(m) => setMode(n.id, m)} />}
                </div>
              );
            })}
          </div>
        </div>

        {/* Zoom widget */}
        <div className="zoom-widget" onMouseDown={(e) => e.stopPropagation()}>
          <button onClick={() => zoomBy(setViewport, wrapRef, 1.2)}>+</button>
          <div className="z-level">{Math.round(viewport.k * 100)}%</div>
          <button onClick={() => zoomBy(setViewport, wrapRef, 1 / 1.2)}>−</button>
        </div>

        {/* Legend */}
        {t.showLegend && (
          <div className="legend">
            <div className="lh">Legend</div>
            <div className="row">
              <span className="sw" style={{ background: 'var(--node-hub)', borderColor: 'var(--node-hub)' }} />
              <span>MLSS Hub · whole-room</span>
            </div>
            <div className="row">
              <span className="sw" style={{ background: 'var(--node-grow)', borderColor: 'var(--node-grow)' }} />
              <span>Grow Unit · per-plant</span>
            </div>
            <div className="row">
              <span className="sw" style={{ background: 'var(--node-eff)', borderColor: 'var(--node-eff)' }} />
              <span>Effector · actuator</span>
            </div>
            <div className="row" style={{ marginTop: 4 }}>
              <span className="sw" style={{
                background: 'transparent', borderColor: 'var(--status-nominal)',
              }}>
                <i style={{ width: 6, height: 1, background: 'var(--status-nominal)' }} />
              </span>
              <span>Edge · on / flowing</span>
            </div>
            <div className="row">
              <span className="sw" style={{
                background: 'transparent', borderColor: 'var(--status-off)',
              }} />
              <span>Edge · off</span>
            </div>
            <div style={{
              fontSize: 9, color: 'var(--text-tertiary)', marginTop: 6,
              fontFamily: 'var(--font-mono)', letterSpacing: '0.05em',
            }}>
              drag nodes · wheel to zoom · click to configure
            </div>
          </div>
        )}

        {/* Minimap */}
        {t.showMinimap && (
          <Minimap nodes={nodes} positions={positions} viewport={viewport}
                   containerRef={wrapRef} />
        )}
      </div>

      {/* ─── Status bar ─── */}
      <footer className="statusbar">
        <span className="seg"><span className="dot" /> SSE · /events</span>
        <span className="seg">14 sub · 0 backlog</span>
        <span className="seg">Δt {now.getSeconds().toString().padStart(2, '0')}s</span>
        <span className="right">
          {now.toISOString().slice(0, 19).replace('T', ' ')}Z
        </span>
      </footer>

      {/* ─── Side panel ─── */}
      {selected && (
        <SidePanel node={selected} allNodes={nodes}
                   onClose={() => setSelectedId(null)}
                   onMode={setMode}
                   onReparent={setReparent}
                   onPower={setPower} />
      )}

      {/* ─── Tweaks ─── */}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Topology" />
        <TweakRadio label="Edge style" value={t.edgeStyle}
                    options={['straight', 'ortho', 'bezier']}
                    onChange={(v) => setTweak('edgeStyle', v)} />
        <TweakRadio label="Live feel" value={t.liveFeel}
                    options={['subtle', 'medium', 'heavy']}
                    onChange={(v) => setTweak('liveFeel', v)} />

        <TweakSection label="Nodes" />
        <TweakRadio label="Card style" value={t.nodeStyle}
                    options={['outline', 'solid']}
                    onChange={(v) => setTweak('nodeStyle', v)} />
        <TweakRadio label="Density" value={t.density}
                    options={['compact', 'regular']}
                    onChange={(v) => setTweak('density', v)} />
        <TweakRadio label="Color coding" value={t.colorMode}
                    options={['type', 'status']}
                    onChange={(v) => setTweak('colorMode', v)} />
        <TweakToggle label="Iconography" value={t.icons}
                     onChange={(v) => setTweak('icons', v)} />

        <TweakSection label="Canvas" />
        <TweakSelect label="Background" value={t.background}
                     options={['dots', 'grid', 'grid-fine', 'stars', 'base']}
                     onChange={(v) => setTweak('background', v)} />
        <TweakToggle label="Legend" value={t.showLegend}
                     onChange={(v) => setTweak('showLegend', v)} />
        <TweakToggle label="Minimap" value={t.showMinimap}
                     onChange={(v) => setTweak('showMinimap', v)} />
      </TweaksPanel>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Helpers

function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

function deriveAutoState(eff, allNodes) {
  // Mock automation — flip a coin biased by role
  // (In a real system this consults the hub's setpoints)
  const parent = allNodes.find((n) => n.id === eff.parent);
  if (!parent || !parent.sensors) return Math.random() > 0.5 ? 'on' : 'off';
  const s = parent.sensors;
  if (eff.role === 'heating')  return s.temp < 24 ? 'on' : 'off';
  if (eff.role === 'cooling')  return s.temp > 26 ? 'on' : 'off';
  if (eff.role === 'humidity') return s.rh < 50 ? 'on' : 'off';
  if (eff.role === 'lighting') return new Date().getHours() < 20 ? 'on' : 'off';
  return Math.random() > 0.5 ? 'on' : 'off';
}

function missionTime(now) {
  // Just a fake "T+ ddd hh:mm:ss" starting from a fixed launch date
  const launch = new Date('2026-01-12T08:00:00Z');
  const diff = Math.max(0, now - launch);
  const ddd = Math.floor(diff / 86400000);
  const hh = Math.floor((diff % 86400000) / 3600000);
  const mm = Math.floor((diff % 3600000) / 60000);
  const ss = Math.floor((diff % 60000) / 1000);
  return `${String(ddd).padStart(3, '0')} ${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function zoomBy(setViewport, wrapRef, factor) {
  const r = wrapRef.current.getBoundingClientRect();
  const mx = r.width / 2;
  const my = r.height / 2;
  setViewport((v) => {
    const newK = clamp(v.k * factor, 0.3, 2.5);
    const wx = (mx - v.x) / v.k;
    const wy = (my - v.y) / v.k;
    return { k: newK, x: mx - wx * newK, y: my - wy * newK };
  });
}

// ─── Minimap ────────────────────────────────────────────────
function Minimap({ nodes, positions, viewport, containerRef }) {
  const W = 200, H = 130;
  // Compute world bounding box
  const xs = Object.values(positions).map((p) => p.x);
  const ys = Object.values(positions).map((p) => p.y);
  const minX = Math.min(...xs) - 100, maxX = Math.max(...xs) + 100;
  const minY = Math.min(...ys) - 80, maxY = Math.max(...ys) + 80;
  const wWorld = maxX - minX;
  const hWorld = maxY - minY;
  const scale = Math.min(W / wWorld, H / hWorld);
  const ox = (W - wWorld * scale) / 2;
  const oy = (H - hWorld * scale) / 2;

  const r = containerRef.current?.getBoundingClientRect();
  if (!r) return null;
  // Visible world rect in container
  const visW = r.width / viewport.k;
  const visH = r.height / viewport.k;
  const visX = (-viewport.x) / viewport.k;
  const visY = (-viewport.y) / viewport.k;

  return (
    <div className="minimap" onMouseDown={(e) => e.stopPropagation()}>
      <svg width={W} height={H}>
        {nodes.map((n) => {
          const p = positions[n.id];
          if (!p) return null;
          const cx = ox + (p.x - minX) * scale;
          const cy = oy + (p.y - minY) * scale;
          const color = n.kind === 'hub' ? '#4dacff' :
                        n.kind === 'grow' ? '#56f000' : '#ffb302';
          const size = n.kind === 'hub' ? 5 : n.kind === 'grow' ? 4 : 3;
          return <rect key={n.id} x={cx - size / 2} y={cy - size / 2}
                       width={size} height={size} fill={color} />;
        })}
        <rect
          x={ox + (visX - minX) * scale}
          y={oy + (visY - minY) * scale}
          width={visW * scale}
          height={visH * scale}
          fill="rgba(77,172,255,0.1)"
          stroke="#4dacff"
          strokeWidth="1"
        />
      </svg>
    </div>
  );
}

// ─── Mount ──────────────────────────────────────────────────
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
