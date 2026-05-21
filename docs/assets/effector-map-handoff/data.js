// MLSS mock topology — nodes & their parent links
// kind: 'hub' | 'grow' | 'effector'
// For effectors: parent is the node they serve; mode ∈ {auto, on, off}; state derived (or set when manual)
// Live values are simulated via a tick loop in app.jsx — values change but topology is fixed.

window.MLSS_NODES = [
  // ── Central hub ────────────────────────────────────────────
  {
    id: 'hub',
    kind: 'hub',
    label: 'MLSS Hub',
    sub: 'central coordinator · grow tent A',
    sensors: { temp: 23.4, rh: 58, co2: 612, lux: 18400 },
    notes: 'Whole-room sensors. Coordinates room-level effectors and dispatches per-unit setpoints.',
  },

  // ── Room-level effectors (parent: hub) ──────────────────────
  {
    id: 'fan',
    kind: 'effector',
    label: 'Room Fan',
    model: 'Vornado 660',
    parent: 'hub',
    mode: 'on',         // auto | on | off (user-set)
    state: 'on',        // actual physical state
    power: 42,          // %
    role: 'circulation',
  },
  {
    id: 'ac',
    kind: 'effector',
    label: 'AC Unit',
    model: 'LG LW8016ER',
    parent: 'hub',
    mode: 'auto',
    state: 'off',
    power: 0,
    setpoint: 22.5,
    role: 'cooling',
  },
  {
    id: 'dehu',
    kind: 'effector',
    label: 'Dehumidifier',
    model: 'Frigidaire FFAD2233W',
    parent: 'hub',
    mode: 'auto',
    state: 'off',
    power: 0,
    setpoint: 55,
    role: 'humidity',
  },
  {
    id: 'co2',
    kind: 'effector',
    label: 'CO₂ Injector',
    model: 'Autopilot APCETL',
    parent: 'hub',
    mode: 'off',
    state: 'off',
    power: 0,
    role: 'co2',
  },

  // ── Grow units (parent: hub) ────────────────────────────────
  {
    id: 'grow1',
    kind: 'grow',
    label: 'Grow #1',
    plant: 'Chocolate habanero',
    stage: 'flowering',
    parent: 'hub',
    sensors: { temp: 26.2, rh: 72, soil: 41, ph: 6.4 },
  },
  {
    id: 'grow2',
    kind: 'grow',
    label: 'Grow #2',
    plant: 'Sweet basil',
    stage: 'vegetative',
    parent: 'hub',
    sensors: { temp: 24.8, rh: 68, soil: 53, ph: 6.6 },
  },
  {
    id: 'grow3',
    kind: 'grow',
    label: 'Grow #3',
    plant: 'Cherry tomato',
    stage: 'seedling',
    parent: 'hub',
    sensors: { temp: 25.1, rh: 75, soil: 60, ph: 6.2 },
  },
  {
    id: 'grow4',
    kind: 'grow',
    label: 'Grow #4',
    plant: 'Albion strawberry',
    stage: 'fruiting',
    parent: 'hub',
    sensors: { temp: 22.6, rh: 65, soil: 38, ph: 6.0 },
  },

  // ── Per-grow effectors ──────────────────────────────────────
  // Grow 1 — habanero
  { id: 'heat1',  kind: 'effector', label: 'Heat Pad',       model: 'VIVOSUN 10×20"',         parent: 'grow1', mode: 'on',   state: 'on',  power: 18, role: 'heating' },
  { id: 'hum1',   kind: 'effector', label: 'Mini-Humidifier', model: 'AquaOasis 0.7L',         parent: 'grow1', mode: 'off',  state: 'off', power: 0,  role: 'humidity' },
  { id: 'light1', kind: 'effector', label: 'Grow Light',     model: 'Spider Farmer SF-1000',  parent: 'grow1', mode: 'auto', state: 'on',  power: 95, role: 'lighting' },

  // Grow 2 — basil
  { id: 'light2', kind: 'effector', label: 'Grow Light',     model: 'Mars Hydro TS-600',      parent: 'grow2', mode: 'on',   state: 'on',  power: 60, role: 'lighting' },
  { id: 'pump2',  kind: 'effector', label: 'Drip Pump',      model: 'EcoPlus 100 GPH',        parent: 'grow2', mode: 'auto', state: 'off', power: 0,  role: 'irrigation' },

  // Grow 3 — tomato seedlings
  { id: 'light3', kind: 'effector', label: 'Grow Light',     model: 'Spider Farmer SF-1000',  parent: 'grow3', mode: 'auto', state: 'on',  power: 70, role: 'lighting' },
  { id: 'pump3',  kind: 'effector', label: 'Mist Pump',      model: 'Hydrofarm AAPW250',      parent: 'grow3', mode: 'auto', state: 'off', power: 0,  role: 'irrigation' },
  { id: 'heat3',  kind: 'effector', label: 'Heat Pad',       model: 'VIVOSUN 10×20"',         parent: 'grow3', mode: 'auto', state: 'on',  power: 22, role: 'heating' },

  // Grow 4 — strawberry
  { id: 'fan4',   kind: 'effector', label: 'Circulation Fan', model: 'AC Infinity S6',        parent: 'grow4', mode: 'on',   state: 'on',  power: 32, role: 'circulation' },
  { id: 'light4', kind: 'effector', label: 'Grow Light',     model: 'Mars Hydro TS-1000',     parent: 'grow4', mode: 'auto', state: 'on',  power: 80, role: 'lighting' },
];

// All grow-unit and room-effector edges hang off the hub.
// Each effector edge: parent → effector (color follows effector state).
// Each grow-unit edge: hub → grow (neutral, denoting subsystem hierarchy).
window.MLSS_ROLES = {
  circulation: { label: 'Circulation', icon: 'fan' },
  cooling:     { label: 'Cooling',     icon: 'ac' },
  heating:     { label: 'Heating',     icon: 'heat' },
  humidity:    { label: 'Humidity',    icon: 'humidifier' },
  lighting:    { label: 'Lighting',    icon: 'light' },
  irrigation:  { label: 'Irrigation',  icon: 'pump' },
  co2:         { label: 'CO₂',         icon: 'co2' },
};
