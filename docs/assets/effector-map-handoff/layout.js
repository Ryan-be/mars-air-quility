// Hierarchical auto-layout for MLSS nodes.
// Hub at center (0,0). Direct children (room effectors + grow units) on a ring.
// Each grow unit's effectors cluster around it.
// Manual drags override and persist to localStorage.

(function () {
  const STORAGE_KEY = 'mlss.node-positions.v2';

  const ROOM_EFFECTOR_RING_R = 320;
  const GROW_RING_R = 360;
  const SUB_EFFECTOR_R = 175;

  function deg(d) { return (d * Math.PI) / 180; }

  function autoLayout(nodes) {
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const positions = {};

    // 1. Hub at origin
    const hub = nodes.find((n) => n.kind === 'hub');
    positions[hub.id] = { x: 0, y: 0 };

    // 2. Room effectors → top arc (spread across angles 200°-340°, i.e. above hub)
    const roomEffectors = nodes.filter(
      (n) => n.kind === 'effector' && n.parent === hub.id,
    );
    const roomCount = roomEffectors.length;
    roomEffectors.forEach((n, i) => {
      // spread across 200°-340° (left to right above hub)
      const startA = -160; // upper-left
      const endA = -20;    // upper-right
      const a = roomCount === 1
        ? -90
        : startA + (endA - startA) * (i / (roomCount - 1));
      positions[n.id] = {
        x: Math.cos(deg(a)) * ROOM_EFFECTOR_RING_R,
        y: Math.sin(deg(a)) * ROOM_EFFECTOR_RING_R,
      };
    });

    // 3. Grow units → bottom arc (20°-160°)
    const grows = nodes.filter((n) => n.kind === 'grow');
    const growCount = grows.length;
    const growAngles = {};
    grows.forEach((n, i) => {
      const startA = 20;
      const endA = 160;
      const a = growCount === 1
        ? 90
        : startA + (endA - startA) * (i / (growCount - 1));
      growAngles[n.id] = a;
      positions[n.id] = {
        x: Math.cos(deg(a)) * GROW_RING_R,
        y: Math.sin(deg(a)) * GROW_RING_R,
      };
    });

    // 4. Each grow's effectors → cluster around the grow node, arc facing
    //    away from hub
    grows.forEach((grow) => {
      const subs = nodes.filter(
        (n) => n.kind === 'effector' && n.parent === grow.id,
      );
      if (!subs.length) return;
      const baseA = growAngles[grow.id]; // angle from hub to this grow
      // Children fan out on the far side of grow (angles around baseA ± 60°)
      const spread = 100; // total degrees
      const halfSpread = spread / 2;
      subs.forEach((s, i) => {
        const t = subs.length === 1 ? 0.5 : i / (subs.length - 1);
        const a = baseA - halfSpread + spread * t;
        positions[s.id] = {
          x: positions[grow.id].x + Math.cos(deg(a)) * SUB_EFFECTOR_R,
          y: positions[grow.id].y + Math.sin(deg(a)) * SUB_EFFECTOR_R,
        };
      });
    });

    return positions;
  }

  function loadPositions() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function savePositions(positions) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(positions));
    } catch (e) {
      /* quota etc — ignore */
    }
  }

  function resetPositions() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  window.MLSS_LAYOUT = {
    autoLayout,
    loadPositions,
    savePositions,
    resetPositions,
  };
}());
