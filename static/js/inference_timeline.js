/**
 * inference_timeline.js — rux-timeline renderer for inference events.
 *
 * Usage:
 *   const timeline = createInferenceTimeline({
 *     timelineContainerId: 'diTimeline',
 *     openDialog: fn(id),
 *     getRange: fn() -> { start, end }  // ISO strings
 *   });
 *   timeline.render(inferences);
 */
'use strict';

window.createInferenceTimeline = function createInferenceTimeline({
  timelineContainerId,
  openDialog,
  getRange,
}) {
  // ── Track definitions ────────────────────────────────────────────────────
  const TRACKS = [
    { label: 'Critical Alerts', status: 'critical',  test: inf => inf.severity === 'critical' },
    { label: 'Warnings',        status: 'serious',   test: inf => inf.severity === 'warning' },
    { label: 'ML Detections',   status: 'caution',   test: inf => inf.detection_method === 'ml' },
    { label: 'Statistical',     status: 'standby',   test: inf => inf.detection_method === 'statistical' },
    { label: 'Rule / Reports',  status: 'normal',    test: () => true },
  ];

  // ── Severity → rux-time-region status ───────────────────────────────────
  function _severityToStatus(inf) {
    if (inf.severity === 'critical') return 'critical';
    if (inf.severity === 'warning')  return 'serious';
    return 'caution';
  }

  // ── Derive end time from an inference ───────────────────────────────────
  function _deriveEnd(inf) {
    const start = new Date(inf.created_at);
    let durationMs;

    if (inf.category === 'summary' || inf.category === 'report') {
      durationMs = 60 * 60 * 1000; // 60 min
    } else if (inf.detection_method === 'ml' && inf.event_type && inf.event_type.includes('occupancy')) {
      durationMs = 8 * 60 * 1000; // 8 min
    } else if (inf.detection_method === 'ml') {
      durationMs = 2 * 60 * 1000; // 2 min
    } else if (inf.detection_method === 'statistical') {
      durationMs = 20 * 60 * 1000; // 20 min
    } else {
      durationMs = 10 * 60 * 1000; // 10 min default
    }

    return new Date(start.getTime() + durationMs).toISOString();
  }

  // ── Check if event extends outside the given range ───────────────────────
  function _isPartial(infStart, infEnd, rangeStart, rangeEnd) {
    const s = new Date(infStart).getTime();
    const e = new Date(infEnd).getTime();
    const rs = new Date(rangeStart).getTime();
    const re = new Date(rangeEnd).getTime();
    return s < rs || e > re;
  }

  // ── Assign inferences to tracks (first matching wins) ───────────────────
  function _assignToTracks(inferences) {
    const assigned = new Set();
    return TRACKS.map(track => {
      const items = inferences.filter(inf => {
        if (inf.dismissed) return false;
        if (assigned.has(inf.id)) return false;
        if (track.test(inf)) {
          assigned.add(inf.id);
          return true;
        }
        return false;
      });
      return { ...track, items };
    });
  }

  // ── Build the rux-timeline DOM ───────────────────────────────────────────
  function render(inferences) {
    const container = document.getElementById(timelineContainerId);
    if (!container) {
      console.warn('[inference_timeline] container not found:', timelineContainerId);
      return;
    }

    // Clear previous content
    container.innerHTML = '';

    const active = (inferences || []).filter(inf => !inf.dismissed);

    if (!active.length) {
      container.innerHTML = '<div class="tl-empty-msg">No inference events in this range.</div>';
      return;
    }

    const { start, end } = getRange();
    const now = new Date().toISOString();

    // Create rux-timeline
    const tl = document.createElement('rux-timeline');
    tl.setAttribute('start', start);
    tl.setAttribute('end', end);
    tl.setAttribute('playhead', now);
    tl.setAttribute('interval', 'hour');
    tl.setAttribute('zoom', '2');
    tl.setAttribute('show-grid', '');
    tl.setAttribute('show-secondary-ruler', '');
    tl.setAttribute('timezone', 'UTC');

    const tracks = _assignToTracks(active);

    tracks.forEach(track => {
      const ruxTrack = document.createElement('rux-track');

      // Track header label
      const header = document.createElement('div');
      header.setAttribute('slot', 'label');
      header.className = 'tl-track-label';
      header.textContent = track.label;
      ruxTrack.appendChild(header);

      track.items.forEach(inf => {
        const infStart = inf.created_at;
        const infEnd   = _deriveEnd(inf);
        const status   = _severityToStatus(inf);
        const title    = (inf.title || '').slice(0, 30);

        const region = document.createElement('rux-time-region');
        region.setAttribute('start', infStart);
        region.setAttribute('end', infEnd);
        region.setAttribute('status', status);
        region.setAttribute('hide-timestamp', '');
        region.setAttribute('data-inf-id', inf.id);

        if (_isPartial(infStart, infEnd, start, end)) {
          region.setAttribute('partial', '');
        }

        region.textContent = title;

        region.addEventListener('click', () => {
          openDialog(inf.id);
        });

        ruxTrack.appendChild(region);
      });

      tl.appendChild(ruxTrack);
    });

    container.appendChild(tl);
  }

  return { render };
};
