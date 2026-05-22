// Sparkline component — used in hub + grow telemetry. Pure SVG.
function Sparkline({ values, color, height = 24 }) {
  if (!values || values.length < 2) return null;
  const w = 100; // viewBox width — scales to container
  const h = height;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(0.001, max - min);
  const stepX = w / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = i * stepX;
    const y = h - 2 - ((v - min) / range) * (h - 4);
    return [x, y];
  });
  const line = pts.map(([x, y], i) => (i === 0 ? `M${x} ${y}` : `L${x} ${y}`)).join(' ');
  const area = `${line} L${w} ${h} L0 ${h} Z`;
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{ height, '--node-color': color || undefined }}>
      <path className="area" d={area} />
      <path className="line" d={line} />
    </svg>
  );
}

// ─── Status pill ────────────────────────────────────────────
function StatusPill({ state, label, blink = false, solid = false }) {
  const color = {
    on: 'var(--status-nominal)',
    off: 'var(--status-off)',
    auto: 'var(--status-standby)',
    fault: 'var(--status-critical)',
  }[state] || 'var(--text-secondary)';
  return (
    <span className={`spill${solid ? ' solid' : ''}${blink ? ' blink' : ''}`}
          style={{ '--state-color': color }}>
      <i className="ball" />
      {label || state}
    </span>
  );
}

// ─── Mode bar: Auto / ON / OFF (segmented) ──────────────────
function ModeBar({ mode, onChange, compact = false }) {
  const modes = ['auto', 'on', 'off'];
  return (
    <div className="modebar" onMouseDown={(e) => e.stopPropagation()}>
      {modes.map((m) => (
        <button key={m} type="button"
                className={mode === m ? 'modebtn on' : 'modebtn'}
                data-mode={m}
                onClick={(e) => { e.stopPropagation(); onChange(m); }}>
          {m === 'auto' ? 'AUTO' : m === 'on' ? 'ON' : 'OFF'}
        </button>
      ))}
    </div>
  );
}

// ─── Hub card ───────────────────────────────────────────────
function HubCard({ node, history }) {
  const HubIc = Icons.hub;
  return (
    <div className="card" style={{ '--node-color': 'var(--node-hub)', minWidth: 220 }}>
      <span className="ledge" />
      <span className="topedge" />
      <div className="card-head">
        <HubIc size={16} />
        <div>
          <div className="card-title">{node.label}</div>
          <div className="card-sub">{node.sub}</div>
        </div>
        <span className="id">{node.id}</span>
      </div>
      <div className="tgrid">
        <div>
          <div className="k">Temp</div>
          <div className="v">{node.sensors.temp.toFixed(1)}<small>°C</small></div>
        </div>
        <div>
          <div className="k">RH</div>
          <div className="v">{node.sensors.rh.toFixed(0)}<small>%</small></div>
        </div>
        <div>
          <div className="k">CO₂</div>
          <div className="v">{node.sensors.co2.toFixed(0)}<small>ppm</small></div>
        </div>
      </div>
      <Sparkline values={history.temp} color="var(--node-hub)" />
    </div>
  );
}

// ─── Grow card ──────────────────────────────────────────────
function GrowCard({ node, history }) {
  const GrowIc = Icons.grow;
  return (
    <div className="card" style={{ '--node-color': 'var(--node-grow)' }}>
      <span className="ledge" />
      <div className="card-head">
        <GrowIc size={16} />
        <div>
          <div className="card-title">{node.label}</div>
          <div className="card-sub">{node.plant}</div>
        </div>
        <span className="id">{node.stage}</span>
      </div>
      <div className="tgrid">
        <div>
          <div className="k">Temp</div>
          <div className="v">{node.sensors.temp.toFixed(1)}<small>°</small></div>
        </div>
        <div>
          <div className="k">RH</div>
          <div className="v">{node.sensors.rh.toFixed(0)}<small>%</small></div>
        </div>
        <div>
          <div className="k">Soil</div>
          <div className="v">{node.sensors.soil.toFixed(0)}<small>%</small></div>
        </div>
      </div>
      <Sparkline values={history.temp} color="var(--node-grow)" />
    </div>
  );
}

// ─── Effector card ──────────────────────────────────────────
function EffectorCard({ node, onMode }) {
  const Ic = effectorIcon(node.role);
  const isOn = node.state === 'on';
  return (
    <div className="card" style={{ '--node-color': 'var(--node-eff)' }}>
      <span className="ledge" />
      <div className="card-head">
        <Ic size={16} />
        <div style={{ minWidth: 0 }}>
          <div className="card-title">{node.label}</div>
          <div className="card-sub" title={node.model}>{node.model}</div>
        </div>
        <span className="id">{node.id}</span>
      </div>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', marginTop: 6,
      }}>
        <StatusPill state={isOn ? 'on' : 'off'}
                    label={isOn ? `● ON · ${node.power}%` : '○ OFF'}
                    solid={isOn} />
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 10,
          color: 'var(--text-tertiary)', letterSpacing: '0.06em',
          textTransform: 'uppercase',
        }}>
          {node.role}
        </span>
      </div>
      <ModeBar mode={node.mode} onChange={onMode} />
    </div>
  );
}

Object.assign(window, {
  Sparkline, StatusPill, ModeBar,
  HubCard, GrowCard, EffectorCard,
});
