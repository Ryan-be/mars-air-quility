// Side panel for configuring an effector / inspecting a grow unit or hub.
function SidePanel({ node, allNodes, onClose, onMode, onReparent, onPower }) {
  if (!node) return null;
  const isEff = node.kind === 'effector';
  const isGrow = node.kind === 'grow';
  const isHub = node.kind === 'hub';

  const HeadIc = isHub ? Icons.hub :
                 isGrow ? Icons.grow :
                 effectorIcon(node.role);

  const nodeColor =
    isHub ? 'var(--node-hub)' :
    isGrow ? 'var(--node-grow)' :
    'var(--node-eff)';

  const parents = allNodes.filter(
    (n) => (n.kind === 'hub' || n.kind === 'grow') && n.id !== node.id,
  );

  return (
    <aside className={`side${node ? ' open' : ''}`} style={{ '--node-color': nodeColor }}>
      <div className="side-hd">
        <HeadIc size={18} className="ic" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <span className="title">{node.label}</span>
          <span className="id">
            {isEff && `${node.role} · `}{node.id}
          </span>
        </div>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="side-body">
        {/* ── Effector controls ───────────────────────────────── */}
        {isEff && (
          <>
            <section className="sect">
              <div className="sect-h">Mode</div>
              <div className="bigseg">
                {['auto', 'on', 'off'].map((m) => (
                  <button key={m}
                          className={node.mode === m ? 'on' : ''}
                          data-mode={m}
                          onClick={() => onMode(node.id, m)}>
                    {m}
                  </button>
                ))}
              </div>
              <p style={{
                fontSize: 11, color: 'var(--text-tertiary)',
                margin: '4px 0 0', lineHeight: 1.5,
              }}>
                {node.mode === 'auto' &&
                  `Hub controls this effector based on ${roleHint(node.role, node.parent, allNodes)}.`}
                {node.mode === 'on' && 'Forced ON — overrides automation.'}
                {node.mode === 'off' && 'Forced OFF — overrides automation.'}
              </p>
            </section>

            {(node.role !== 'co2') && (
              <section className="sect">
                <div className="sect-h">Power output</div>
                <div className="slider-row">
                  <input type="range" min="0" max="100" step="1"
                         value={node.power}
                         disabled={node.mode === 'off' || node.state === 'off'}
                         onChange={(e) => onPower(node.id, Number(e.target.value))} />
                  <span className="sv">{node.power}%</span>
                </div>
              </section>
            )}

            <section className="sect">
              <div className="sect-h">Belongs to</div>
              <div className="target-pick">
                {parents.map((p) => {
                  const sel = p.id === node.parent;
                  const swatch = p.kind === 'hub'
                    ? 'var(--node-hub)' : 'var(--node-grow)';
                  return (
                    <button key={p.id} className={sel ? 'sel' : ''}
                            onClick={() => onReparent(node.id, p.id)}>
                      <span className="swatch" style={{ background: swatch }} />
                      <span className="lbl">{p.label}</span>
                      <span className="ksub">{p.kind}</span>
                    </button>
                  );
                })}
              </div>
            </section>

            <section className="sect">
              <div className="sect-h">Schedule</div>
              <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 4 }}>
                24-hour cycle · click to toggle hours
              </div>
              <Schedule hours={mockSchedule(node.id)} />
              <div style={{ display: 'flex', justifyContent: 'space-between',
                            fontFamily: 'var(--font-mono)', fontSize: 10,
                            color: 'var(--text-tertiary)', marginTop: 4 }}>
                <span>00</span><span>06</span><span>12</span><span>18</span><span>24</span>
              </div>
            </section>

            <section className="sect">
              <div className="sect-h">Hardware</div>
              <div className="kv-grid">
                <span className="k">Model</span>
                <span className="v">{node.model}</span>
                <span className="k">Role</span>
                <span className="v">{node.role}</span>
                <span className="k">State</span>
                <span className="v" style={{
                  color: node.state === 'on'
                    ? 'var(--status-nominal)' : 'var(--status-off)',
                }}>
                  {node.state.toUpperCase()}
                </span>
                <span className="k">Power draw</span>
                <span className="v">{Math.round(node.power * 0.6)}<small style={{ color: 'var(--text-tertiary)' }}> W</small></span>
                <span className="k">Last switch</span>
                <span className="v">{mockTimeAgo(node.id)}</span>
              </div>
            </section>
          </>
        )}

        {/* ── Grow unit panel ───────────────────────────────── */}
        {isGrow && (
          <>
            <section className="sect">
              <div className="sect-h">Plant</div>
              <div className="kv-grid">
                <span className="k">Species</span>
                <span className="v">{node.plant}</span>
                <span className="k">Stage</span>
                <span className="v" style={{ textTransform: 'uppercase' }}>{node.stage}</span>
                <span className="k">Soil pH</span>
                <span className="v">{node.sensors.ph}</span>
              </div>
            </section>

            <section className="sect">
              <div className="sect-h">Live sensors</div>
              <div className="kv-grid">
                <span className="k">Temperature</span>
                <span className="v">{node.sensors.temp.toFixed(1)} °C</span>
                <span className="k">Humidity</span>
                <span className="v">{node.sensors.rh.toFixed(0)} %</span>
                <span className="k">Soil moisture</span>
                <span className="v">{node.sensors.soil.toFixed(0)} %</span>
              </div>
            </section>

            <section className="sect">
              <div className="sect-h">Linked effectors</div>
              <div className="target-pick">
                {allNodes.filter((n) => n.parent === node.id).map((eff) => (
                  <div key={eff.id} className="sel" style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '8px 10px', cursor: 'default',
                    background: 'transparent', borderColor: 'transparent',
                  }}>
                    <span className="swatch" style={{ background: 'var(--node-eff)' }} />
                    <span className="lbl">{eff.label}</span>
                    <span className="ksub">{eff.state === 'on' ? `● ${eff.power}%` : '○ off'}</span>
                  </div>
                ))}
                {!allNodes.some((n) => n.parent === node.id) && (
                  <div style={{
                    padding: 12, fontSize: 11, color: 'var(--text-tertiary)',
                    textAlign: 'center',
                  }}>No effectors assigned</div>
                )}
              </div>
            </section>
          </>
        )}

        {/* ── Hub panel ─────────────────────────────────────── */}
        {isHub && (
          <>
            <section className="sect">
              <div className="sect-h">Room sensors</div>
              <div className="kv-grid">
                <span className="k">Temperature</span>
                <span className="v">{node.sensors.temp.toFixed(1)} °C</span>
                <span className="k">Humidity</span>
                <span className="v">{node.sensors.rh.toFixed(0)} %</span>
                <span className="k">CO₂</span>
                <span className="v">{node.sensors.co2.toFixed(0)} ppm</span>
                <span className="k">Ambient light</span>
                <span className="v">{node.sensors.lux.toFixed(0)} lux</span>
              </div>
            </section>
            <section className="sect">
              <div className="sect-h">Coordination</div>
              <p style={{
                fontSize: 12, lineHeight: 1.5,
                color: 'var(--text-secondary)', margin: 0,
              }}>
                {node.notes}
              </p>
            </section>
            <section className="sect">
              <div className="sect-h">Subsystems</div>
              <div className="kv-grid">
                <span className="k">Grow units</span>
                <span className="v">{allNodes.filter((n) => n.kind === 'grow').length}</span>
                <span className="k">Effectors</span>
                <span className="v">{allNodes.filter((n) => n.kind === 'effector').length}</span>
                <span className="k">Active now</span>
                <span className="v" style={{ color: 'var(--status-nominal)' }}>
                  {allNodes.filter((n) => n.state === 'on').length}
                </span>
              </div>
            </section>
          </>
        )}
      </div>
    </aside>
  );
}

function roleHint(role, parentId, nodes) {
  const parent = nodes.find((n) => n.id === parentId);
  const tgt = parent && parent.kind === 'grow' ? `${parent.label}'s setpoints`
                                                : 'room-level setpoints';
  return ({
    heating: `${tgt} for temperature`,
    cooling: `${tgt} for temperature`,
    humidity: `${tgt} for relative humidity`,
    lighting: `${tgt} and photoperiod`,
    irrigation: `${tgt} and soil moisture`,
    circulation: `${tgt} for VPD & airflow`,
    co2: `${tgt} for CO₂ concentration`,
  })[role] || tgt;
}

function Schedule({ hours }) {
  return (
    <div className="schedule">
      {hours.map((on, i) => (
        <div key={i} className={`h${on ? ' on' : ''}`}
             title={`${String(i).padStart(2, '0')}:00`} />
      ))}
    </div>
  );
}

// Stable-but-pseudo-random schedule per effector id, just for visual mock
function mockSchedule(id) {
  const h = Array.from({ length: 24 }, () => false);
  let s = 0;
  for (let i = 0; i < id.length; i++) s = (s * 31 + id.charCodeAt(i)) & 0xfffffff;
  // pick two on-windows
  const start1 = s % 8;
  const len1 = 6 + ((s >> 4) % 6);
  const start2 = 12 + ((s >> 8) % 8);
  const len2 = 2 + ((s >> 12) % 4);
  for (let i = 0; i < len1; i++) h[(start1 + i) % 24] = true;
  for (let i = 0; i < len2; i++) h[(start2 + i) % 24] = true;
  return h;
}

function mockTimeAgo(id) {
  let s = 0;
  for (let i = 0; i < id.length; i++) s = (s * 17 + id.charCodeAt(i)) & 0xffff;
  const mins = s % 240;
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
}

Object.assign(window, { SidePanel });
