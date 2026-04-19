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
  // Returns 'ongoing', 'start', 'end', or null (no partial).
  function _isPartial(infStart, infEnd, rangeStart, rangeEnd) {
    const s = new Date(infStart).getTime();
    const e = new Date(infEnd).getTime();
    const rs = new Date(rangeStart).getTime();
    const re = new Date(rangeEnd).getTime();
    if (s < rs && e > re) return 'ongoing';
    if (s < rs) return 'start';
    if (e > re) return 'end';
    return null;
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
    tl.style.minHeight = '250px';
    const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    tl.setAttribute('timezone', browserTz);

    const tracks = _assignToTracks(active);

    // Build inference lookup and overlap map
    const inferenceMap = {};
    active.forEach(inf => { inferenceMap[inf.id] = inf; });

    function _buildOverlapMap(tracks) {
      const map = {};
      tracks.forEach(track => {
        const items = track.items;
        for (let i = 0; i < items.length; i++) {
          for (let j = i + 1; j < items.length; j++) {
            const a = items[i], b = items[j];
            const aS = new Date(a.created_at).getTime(), aE = new Date(_deriveEnd(a)).getTime();
            const bS = new Date(b.created_at).getTime(), bE = new Date(_deriveEnd(b)).getTime();
            if (aS < bE && aE > bS) {
              (map[a.id] = map[a.id] || []).push(b.id);
              (map[b.id] = map[b.id] || []).push(a.id);
            }
          }
        }
      });
      return map;
    }
    const overlapMap = _buildOverlapMap(tracks);

    function _showOverlapPicker(evt, inferences) {
      const existing = document.getElementById('tl-overlap-picker');
      if (existing) existing.remove();
      const picker = document.createElement('div');
      picker.id = 'tl-overlap-picker';
      Object.assign(picker.style, {
        position: 'fixed', zIndex: '9999',
        background: 'var(--color-background-surface-default,#1b2d3e)',
        border: '1px solid var(--color-border-interactive-muted,#2b659b)',
        borderRadius: '4px', padding: '0.25rem 0',
        boxShadow: '0 4px 20px rgba(0,0,0,0.5)',
        minWidth: '200px', maxWidth: '320px',
        left: Math.min(evt.clientX, window.innerWidth - 340) + 'px',
        top: (evt.clientY + 8) + 'px',
      });
      inferences.forEach(inf => {
        const item = document.createElement('div');
        item.style.cssText = 'padding:0.4rem 0.75rem;cursor:pointer;font-size:0.82em;color:var(--color-text-primary,#fff);border-bottom:1px solid rgba(43,101,155,0.3)';
        item.textContent = (inf.title || 'Event #' + inf.id) + ' · ' +
          new Date(inf.created_at).toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' });
        item.addEventListener('mouseenter', () => item.style.background = 'var(--color-background-surface-hover,rgba(77,172,255,0.08))');
        item.addEventListener('mouseleave', () => item.style.background = '');
        item.addEventListener('click', e => { e.stopPropagation(); picker.remove(); openDialog(inf.id); });
        picker.appendChild(item);
      });
      document.body.appendChild(picker);
      setTimeout(() => {
        document.addEventListener('click', function _close(e) {
          if (!picker.contains(e.target)) { picker.remove(); document.removeEventListener('click', _close, true); }
        }, true);
      }, 0);
    }

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

        const partial = _isPartial(infStart, infEnd, start, end);
        if (partial) {
          region.setAttribute('partial', partial);
        }

        region.textContent = title;

        region.addEventListener('click', evt => {
          const overlapping = (overlapMap[inf.id] || []).map(oid => inferenceMap[oid]).filter(Boolean);
          if (overlapping.length === 0) {
            openDialog(inf.id);
          } else {
            evt.stopPropagation();
            _showOverlapPicker(evt, [inf, ...overlapping]);
          }
        });

        ruxTrack.appendChild(region);
      });

      tl.appendChild(ruxTrack);
    });

    container.appendChild(tl);
  }

  return { render };
};
