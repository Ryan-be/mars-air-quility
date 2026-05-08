# Grow UI design critique

**Date:** 2026-05-08
**Scope:** Every grow page on the live system at `https://mlss.local:5000/`,
walked via Claude-in-Chrome MCP (screenshots + accessibility tree) plus
direct inspection of `static/css/grow.css` and the component files under
`static/js/grow/components/`.
**Method:** Page-by-page UX walkthrough → token consistency audit →
prioritised findings.

This is design feedback, not bug reports. Anything that's a functional
bug went to commits already (e.g. fleet-card photo, "buttons gone" /
sensor-event-chart, watering-history empty state).

---

## Page-by-page findings

### Fleet view (`/grow`)

**What I saw:** one card "Chocolate habanaro" with the latest photo
filling ~70% of card height. Status pill "NOMINAL" in the corner.
"+ ADD UNIT" in the very top-right.

**Issues:**

1. **🔴 Photo dominates the card** — `gu-photo` has `aspect-ratio: 4/3`
   but no `max-height`. With a single card on a 1232px viewport, the
   photo renders ~924px tall. Operator scrolls past a wall of photo
   to reach Identify / Open buttons. (Mitigation already in backlog:
   the server-side thumbnail endpoint listed in `Bugs_Improvements_and_Roadmap.md`
   Phase 4. Once shipped, the fleet card should fetch the `?size=thumb`
   variant.)
   * **Fix:** add `max-height: 220px;` to `.gu-photo` in `static/css/grow.css:105`,
     or split `.gu-photo` (full-card) vs `.gu-photo-thumb` (fleet-card).
   * **File:** `static/css/grow.css:105`, `static/js/grow/components/grow-card.mjs:53`

2. **🟡 "+ ADD UNIT" button is in the top-right of the page header,
   far from the cards** — hard to discover when you've just looked at
   the units. Common pattern: put it next to the count summary
   ("1 units · 1 online · 0 stale · 0 offline | + ADD UNIT") in the
   same horizontal row.
   * **File:** `templates/grow_fleet.html` + `static/css/grow.css:29`

3. **🟡 Three filter pill groups (PHASE / STATUS / PLANT) all use the
   same chip style** with no clear visual separation between groups
   — only an uppercase label. With more units / more plant types
   the row will get crowded fast.
   * **File:** `static/css/grow.css:46-72` (`.fleet-filter-row`,
     `.fleet-filter-group`, `.fleet-filter-chip`).

4. **🟢 Status pill "NOMINAL" in dark-on-dark green** is hard to read.
   Green-ish text `#56f000` on `#0c151c` background passes WCAG AA
   for large text but it sits at body size (10px) which is borderline.
   * **File:** `static/css/grow.css:88-94`

---

### Unit detail header

**What I saw:** `← GROW UNITS` back link (small grey caps) above title
"Chocolate habanaro" with three pills next to it: VEGETATIVE (green-bg
dark-text) · SOIL (transparent-bg blue-text) · NOMINAL (green-text on
dark).

**Issues:**

5. **🔴 Three pills use three different visual treatments.** They sit
   next to each other and look like they encode different things,
   but they're all "current state" — unit phase, growth medium,
   liveness. Pick one pill style and apply it consistently with
   only the colour varying by category.
   * VEGETATIVE: `.du-pill.phase` — solid green background
   * SOIL: `.du-pill` — translucent blue tint, blue text
   * NOMINAL: `.du-status` — different element entirely, dark bg
     + green text
   * **Fix:** harmonise to one base style (recommend the translucent
     `.du-pill` look) and vary only `--pill-color` per category.
   * **File:** `static/css/grow.css:126-128, 88-94`

6. **🟡 "← GROW UNITS" back link is barely visible** — `#7d92a8` at
   12px uppercase, sitting above the title. Missing a left-pointing
   chevron (the `←` works but a proper chevron icon would feel less
   like "literal arrow character").
   * **File:** `static/css/grow.css:122`

---

### Unit detail tabs (LIVE / HISTORY / CONFIGURE / DIAGNOSTICS)

**What I saw:** Four pill-with-bottom-border tabs. Active tab has a
thin blue underline. Spacing between tabs feels normal.

**Issues:**

7. **🟢 Active-tab indicator is too subtle.** A 2px blue underline on
   a transparent button is easy to miss when scanning. Even adding
   `font-weight: 500` on `.du-tab.active` would help.
   * **File:** `static/css/grow.css:130-131`

8. **🟢 Inactive tabs are `#7d92a8`** which is the same colour as
   metadata text, reducing scan-ability. Consider raising contrast on
   the inactive state: `#9aa6b2` or `#c2d2e3` so tabs stand out as
   navigation rather than label text.
   * **File:** `static/css/grow.css:130`

---

### Live tab — empty data state

**What I saw:** Photo panel filling viewport. Below: "Live readings"
header (no tiles — no soil sensor wired). Below: "Light schedule ·
vegetative" with a horizontal blue bar showing the schedule.

**Issues:**

9. **🔴 "Live readings" panel renders header-only when there's no
   data.** With camera-only deployment the panel is empty — looks
   broken or like it failed to load. The empty `<div class="du-stat-grid">`
   has no fallback content.
   * **Fix:** when `unit.last_known_state` has no telemetry fields
     populated, render a placeholder ("No telemetry yet — connect
     a soil/light/temp sensor or wait for first reading.") inside
     the panel.
   * **File:** `static/js/grow/unit_detail.mjs::renderLiveReadings`
     around line 95-134

10. **🟡 Light schedule bar has no time labels.** It's a horizontal
    blue bar with a thin green tick (current time?) and no visible
    "00:00" / "12:00" / "23:59" markers. Operator can't tell where in
    the day the green tick is.
    * **File:** `static/js/grow/components/schedule-bar.mjs`

11. **🟡 Photo panel "Latest photo" header has a tiny dot/icon
    glyph** (looks like ▣ or a square) instead of a recognisable
    camera icon. The unicode "📷" works but maybe doesn't render
    correctly on the system font being used.
    * **File:** `static/js/grow/unit_detail.mjs:159` —
      `head.innerHTML = "<span>📷 Latest photo</span>";` confirms
      the source uses the camera emoji; the rendering issue is
      probably a font-substitution. Worth specifying an emoji-
      compatible font fallback in the CSS reset.

---

### Configure tab

**What I saw:**
- Plant profile section: 6 form rows (LABEL / PLANT TYPE / MEDIUM /
  CURRENT PHASE / SOWN AT / DESCRIPTION) + SAVE button
- PID controller: 7 rows, each with `(default)` placeholder + `(DEFAULT)`
  badge + `RESET` ghost button + SAVE
- Light schedule: 5 sub-panels (SEEDLING / VEGETATIVE / FLOWERING /
  FRUITING / DORMANT), each with "+ ADD WINDOW" and "SAVE [PHASE]"
- Soil calibration: "Step 1: place sensor in dry soil, then capture"
  + I'M DRY NOW + SAVE
- Safety override: ACTION dropdown + DURATION (S) + red OVERRIDE button

**Issues:**

12. **🔴 Light schedule has 10 buttons (5 phases × 2 each) when only
    one phase is current.** The non-current phases (SEEDLING,
    FLOWERING, FRUITING, DORMANT) all show "(no windows — using
    profile default)" + idle ADD/SAVE buttons. Visual noise.
    * **Fix:** collapse non-current phase sections into accordion
      headers ("▶ FLOWERING — using profile default"); only the
      current phase is expanded by default. Reduces 10 buttons to 2
      visible.
    * **File:** `static/js/grow/components/light-windows-editor.mjs`

13. **🔴 PID controller has FOUR things on each row**: label / input
    with `(default)` placeholder / `(DEFAULT)` badge / RESET button.
    The two "(default)" elements are visually adjacent and confusing
    — the placeholder text says `(default)` and the badge to the
    right of the input also says `(DEFAULT)`. Operator wonders if
    they're different.
    * **Fix:** drop the `(DEFAULT)` badge. The greyed `(default)`
      placeholder + RESET button is enough signal. Or replace the
      badge with a small "·" status dot (filled = override active).
    * **File:** `static/js/grow/components/pid-editor.mjs` +
      `static/css/grow.css:165-180` (`.cfg-row`, `.cfg-badge`)

14. **🟡 Two buttons of mixed weight in Soil calibration.** "I'M DRY
    NOW" is primary blue (the action you take); "SAVE" is ghost
    (also an action). Calibration is a multi-step wizard so SAVE
    being faded makes sense ("nothing to save yet"), but the visual
    treatment makes the operator wonder if SAVE is disabled.
    * **Fix:** if SAVE is disabled until both readings are taken,
      give it the `disabled` attribute so it shows the disabled
      cursor + stronger fade. The current "looks-like-button-but-
      isn't-active" is misleading.
    * **File:** `static/js/grow/components/calibration-wizard.mjs`

15. **🟡 SAVE buttons appear five times on the Configure tab** (once
    per panel: profile, PID, each light-windows phase, calibration,
    safety override). Each scoped to its own form, which is correct,
    but the visual repetition makes the page feel "save-heavy".
    Consider reducing through:
    * One sticky-bottom "SAVE ALL" button that submits dirty panels
      together; OR
    * Inline "✓ Saved" auto-confirmation when a field loses focus,
      no explicit save action
    * **File:** affects all `cfg-*` editors

16. **🟢 Form labels are uppercase + tracked-out** (`text-transform:
    uppercase; letter-spacing: 0.04em`). Inputs sit right-aligned
    via `flex` but with no fixed label-column width, so labels of
    different lengths ("KP" vs "SOAK WINDOW (MIN)") cause misaligned
    inputs. Use a 2-column grid with a fixed label column instead
    of flex+wrap.
    * **File:** `static/css/grow.css:167` (`.cfg-row`)

---

### Diagnostics tab

(From last night's screenshot in the smoke-test audit.)

**What I saw:** Firmware card with version/uptime/buffer-size. Buffered
messages section. Connection log (a table of online/offline events).
Sensor sanity ("No capabilities reported yet"). Danger zone with three
sub-actions: token rotator, decommission, clear remote buffer (and
soon: clear all photos from last night's commit).

**Issues:**

17. **🟡 Connection log shows ~20 rows of online/offline timestamps
    in a plain table.** No grouping, no relative time ("2m ago"),
    no visual flow. Becomes a wall of timestamps quickly.
    * **Fix:** swap each row's "online"/"offline" word for a
      coloured status dot + relative time. Group consecutive
      events into a single "12 reconnects in 2 minutes" row when
      they happen rapidly.
    * **File:** `static/js/grow/components/connection-log.mjs`

18. **🟢 Danger zone subsections all use the same red border + warning
    head, but the actions inside are progressively more dangerous.**
    Token rotation is recoverable; decommission needs type-the-label
    confirm; clear-photos (new) is recoverable but destructive of
    test data; clear-remote-buffer is "lose un-replayed telemetry".
    Currently they're all visually equivalent.
    * **Fix:** add a per-action danger ramp via a small icon: 🔄
      (rotate, recoverable) / 🗑 (clear data) / ⚠ (decommission,
      destructive). Already partially present but inconsistent.

---

### Errors page (`/grow/errors`)

**What I saw:** Severity filter chips (info / warning / critical),
Kind dropdown, "Unresolved only" checkbox, Refresh button. Below: a
list of every "unit online" reconnect, each with `Resolve / Snooze 1h
/ Snooze 24h` buttons.

**Issues:**

19. **🔴 Reconnect events should not be filed as `grow_errors` at
    all** (this matches Anomaly #1 from yesterday's smoke-test audit
    — keeping it surfaced here as a design issue too). The Errors
    page is dominated by harmless `info`-severity reconnects, drowning
    out actual alerts. Diagnostics → Connection log is the right
    home for this churn.
    * **Action:** filter `kind="online"` out of the default
      `/grow/errors` view server-side.
    * **File:** `mlss_monitor/routes/api_grow_errors.py` (or
      wherever the list endpoint lives).

20. **🟡 Each error row has THREE buttons** (Resolve / Snooze 1h /
    Snooze 24h) — equal visual weight. Resolve is the common
    action; snooze is rarely used. Currently they're all default
    style which encourages accidental snooze.
    * **Fix:** primary = Resolve; ghost = Snooze (collapsed into a
      single "Snooze ▾" dropdown showing 1h/24h options).

---

### Settings page (`/grow/settings`)

**What I saw:** Enrollment key rotator section, plant profiles
list (15 cards), holiday mode toggle.

**Issues:**

21. **🟢 Plant profile cards are buttons** (clickable to edit) but
    don't look interactive — same flat panel as info tiles. A subtle
    hover state + cursor:pointer affordance would help.
    * **File:** `static/css/grow.css` (probably `.pp-list-row` or
      similar — `static/js/grow/grow_settings/profile-list.mjs`)

22. **🟢 "Loaded 15 profiles." footer text** is fine but feels like
    debug output. Could be removed once the list itself is
    self-evidently populated.

---

## Cross-cutting design token issues

Audit of `static/css/grow.css` shows tokens drifting:

### Letter-spacing — six values for what should be three categories

| Value | Used in | Recommended bucket |
|---|---|---|
| `0.02em` | `.gu-meta` | Body |
| `0.03em` | (my new) `.ps-hour-cell` | (consolidate) |
| `0.04em` | `.du-panel-head`, `.cfg-badge` | Headlines |
| `0.05em` | `.du-back` | (consolidate) |
| `0.06em` | `.du-act-btn`, `.cfg-reset` | Small caps |
| `0.08em` | `.gu-status`, `.du-tab`, `.gu-btn` | Small caps |

**Recommendation:** standardise to 3 values:
- `--ls-body: 0`
- `--ls-headline: 0.04em`
- `--ls-allcaps: 0.08em`

### Font-size — five different small sizes

`9px / 10px / 11px / 12px / 13px` all in active use across components.
**Recommendation:** collapse to `10px / 12px / 14px` for "small caps /
body / heading".

### Background colours — five different "dark" blacks

`#0a1219 / #0e1722 / #142028 / #0c151c / #080c11`. Three semantic roles
(page bg, panel bg, sub-panel bg) would be enough.
**Recommendation:** define `--bg-page`, `--bg-panel`, `--bg-sub`.

### Border colours — three variants

`#1c2733 / #1f2e3c / #2a3d50`. Two roles (subtle separator vs panel
border) suffice.

### Text colours — five greys

`#fff / #c2d2e3 / #d6dde4 / #7d92a8 / #9aa6b2`. The `#c2d2e3` and
`#d6dde4` differ by 8% lightness — almost certainly a copy-paste
inconsistency. `#7d92a8` and `#9aa6b2` are similarly close.

---

## Priority list (recommended fix order)

If we cherry-pick the highest-impact items first:

1. **Photo size cap on fleet card + Live tab** (#1) — biggest UX
   impact, single CSS change. Pairs with the thumbnail endpoint
   already on the polish backlog.
2. **Reconnect events filtered out of /grow/errors** (#19) — page
   currently noise-dominated; minor server change.
3. **Empty-state placeholders on Live readings + Watering history**
   (#9) — first-deployment / camera-only operators see broken-
   looking panels otherwise. (Watering history empty state: already
   landed in the sensor-event-chart fix from this morning.)
4. **Light-schedule phase accordion** (#12) — turns 10 buttons into
   2 visible.
5. **Pill style harmonisation in unit-detail header** (#5).
6. **PID controller `(DEFAULT)` badge removal** (#13).
7. **Design token consolidation** (cross-cutting) — pre-work for
   any future dark-mode/light-mode toggle, plus reduces accidental
   drift as the codebase grows.

The rest are polish — worth a swing on a quiet afternoon but not
critical.
