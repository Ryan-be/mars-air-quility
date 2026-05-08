# Plant Grow Unit — E2E coverage gap analysis (2026-05-08)

Audit of why six bugs reached physical deployment despite the e2e suite being green. Branch: `feature/plant-grow-units`.

## E2e test inventory

All e2e files live under `tests/grow_server/`. There are **none** under `tests/grow_unit/` (the grow_unit package has unit + integration tests but no e2e file matching `test_*_e2e.py`).

- **`tests/grow_server/test_e2e_smoke.py`** — One asyncio test, `test_full_lifecycle`. Boots a real WS listener on a random port + a minimal Flask app with only `api_grow_enroll_bp`. A real `websockets.connect` enrols, sends a capabilities frame, a telemetry frame, one binary photo frame, and the server pushes one identify command. Assertions are pure DB row counts (`grow_unit_capabilities`, `grow_telemetry`, `grow_photos`) and the existence of the photo file on disk. **No** browser, **no** JS, **no** GET to fleet/detail endpoints, no Plotly, no template render. The photo it inserts is never read back through the photo-URL surface.

- **`tests/grow_server/test_install_flow_e2e.py`** — Two tests covering the bash installer flow. Builds a tiny Flask app with only `api_grow_dist_bp`, hits `/api/grow/dist/latest`, downloads the wheels, and verifies sha256 hashes match. No DB seeding beyond the empty schema. Architectural limit: only exercises the dist endpoint — no firmware lifecycle, no UI, no template.

- **`tests/grow_server/test_phase3_diagnostics_e2e.py`** — Nine tests boots the real `mlss_monitor.app.app` with `state.github_oauth` mocked truthy (auth-on posture) and (for some tests) a real WS listener with a `_FakeFirmware` client. Exercises the `/diagnostics`, `/errors`, `/clear-buffer`, soft-delete and storage-warning routes through the production blueprint registration + admin/viewer sessions. Test 8 renders `/grow` and asserts substring presence of the storage banner; this is the *only* e2e test that touches a Jinja template render. Architectural limit: still no JS execution. The "/grow renders banner" assertion uses raw substring match on the HTML, not a parsed DOM.

- **`tests/grow_server/test_configure_e2e.py`** — Eight async tests boots real Flask app + real WS listener + a `_FakeFirmware` that drains command frames. Tests every Configure endpoint (`PUT /profile`, `/pid`, `/light_windows`, `/calibration`, `POST /safety_override`, etc.), asserts the WS push lands at the firmware AND the DB row updates AND audit rows exist. Test 8 covers offline-edit-then-reconnect. Architectural limit: server-side only — no browser/JS, no template render. Test 1 PUTs `phase=flowering` but never opens `/grow/units/<id>` in a way that runs the page's JS.

- **`tests/grow_server/test_grow_authz_e2e.py`** — Six small tests that boot the real app + a stub registry, set a cookie session, and assert `/water-now`, `/identify` give 401/403/503 in the right places. Pure HTTP authz checks. No firmware client, no WS, no UI.

- **`tests/grow_server/test_grow_ws_tls_e2e.py`** — Four tests around `wss://` handshake: self-signed cert + `start_ws_listener(ssl_context=...)`, real `websockets.connect`, including pinned-cert positive + negative paths via the real `mlss_grow.WSClient`. Architectural limit: covers TLS only — does not seed any data, does not exercise UI.

- **`tests/grow_server/test_history_e2e.py`** — Seven tests around the History tab data lanes. Boots real Flask app + auth-on, seeds 100 (or 1000) telemetry rows + 10 photo rows + JPEG bytes on disk, hits `/history?range=24h`, `/photos`, `/photos/<id>`, asserts shape (raw vs downsampled keys, ASC ordering, JPEG bytes match, cross-unit 404). **Critically, this is the only file that seeds photos on disk** — but it only tests the History endpoints, never the fleet card surface or the `last_known_state.last_photo_url` join. Test asserts `body["watering_events"]` exists but no test asserts the `sensor-event-chart.mjs` consumer can render those events (no JS).

- **`tests/grow_server/test_sense_only_mode_e2e.py`** — Seven tests around the capability `health` field. Boots real Flask app + admin session, calls `handlers.handle_capabilities/telemetry/event` directly (skipping the WS listener) and `GET /api/grow/units/<id>` to assert the surfaced `health`. Includes the "first-deployment story" Test 7: camera-only → install PSU → reboot → water_now → connected. Architectural limit: server-only — never opens the fleet page or the unit-detail page in a browser, so the JS code path that turns `health="no_hardware"` into greyed-out buttons is unverified.

## Coverage analysis per bug

### Bug 1 — Fleet card "— No photo yet —" placeholder shown despite photos existing
**Root cause recap:** `grow-card.mjs:55` reads `unit.last_known_state?.last_photo_url`; `_last_known_state` never wrote the key.

**Would have been caught by:** No existing e2e test. Closest near-misses:

- `test_e2e_smoke.py::test_full_lifecycle` does upload a photo via the WS listener and verify the row + file land — but it never calls `GET /api/grow/units` afterwards, so the missing key in the response is not asserted.
- `test_history_e2e.py` seeds photos and serves `/photos/<id>`, but never hits `/api/grow/units` (the fleet endpoint) and never asserts on `last_known_state`.
- `test_sense_only_mode_e2e.py` calls `GET /api/grow/units/<id>` repeatedly but only inspects the `capabilities` block, never `last_known_state`.

**Why no test caught it:** The bug was a server-side contract gap (response missing a key) that only manifested visually. The e2e suite never *reads* the field on the response side. The `tests/grow_server/test_grow_units_api.py` *unit* test for `last_photo_url` was added as part of the fix (`94b08aa`) — but pre-fix there was no contract test for what `_last_known_state` must contain.

**Cheapest test to add:** Extend `test_e2e_smoke.py` to add a Flask client `GET /api/grow/units` after the WS lifecycle and assert `body["units"][0]["last_known_state"]["last_photo_url"]` is non-null and points at the just-uploaded photo's URL. Total: ~10 LoC, reuses fixture, would have failed pre-fix.

---

### Bug 2 — "Plotly not loaded" on unit-detail Live tab
**Root cause recap:** `templates/grow_unit_detail.html` was missing the Plotly script tag; `sensor-event-chart.mjs:25` defensively rendered the literal text.

**Would have been caught by:** No existing e2e test. The closest is `test_phase3_diagnostics_e2e.py::test_e2e_storage_warning_appears_on_grow_page_when_disk_over_threshold` — it does GET `/grow` and substring-matches the rendered HTML — but it never GETs `/grow/units/<id>` (the unit-detail page) and never asserts the presence of the Plotly `<script>` tag.

**Why no test caught it:** No e2e test renders the unit-detail template at all. Even if one did, the bug is a JS-side rendering bug that only surfaces when `Plotly` is undefined at runtime — a Jinja substring assertion would still need to specifically look for the Plotly script src.

**Cheapest test to add (no browser):** Extend the phase3 diagnostics e2e or add one new test that seeds a unit and `GET /grow/units/1`, asserts `cdn.plot.ly/plotly` is in the HTML (substring check). This catches the script-tag absence.

**Better test (real browser):** A Playwright/Selenium test that navigates to `/grow/units/<id>`, switches to the Live tab, and asserts no console error and that the chart container does not contain the literal text "Plotly not loaded".

---

### Bug 3 — Sensor-event chart crashed once Plotly loaded (`data.events` vs `data.watering_events`)
**Root cause recap:** `sensor-event-chart.mjs` read `data.events`, but `/history` returns `data.watering_events`. The bug was masked by Bug 2's early-return guard until that was fixed.

**Would have been caught by:** No existing e2e test runs the JS at all. `test_history_e2e.py::test_history_24h_returns_raw_shape` *does* assert `body["watering_events"]` exists in the JSON envelope — pinning the server contract — but no test asserts that the JS consumer reads the right key. So the contract was unilaterally pinned on the server side and the JS drifted silently.

**Why no test caught it:** Two-sided contract mismatch with no integrating test. The server says `watering_events`; the client says `events`; both ends had unit/integration tests against their own conception.

**Cheapest test to add:** A pure JS unit test (in `tests/js/`, mirroring the existing `tests/js/test_grow_card.mjs` pattern with jsdom) that calls `renderSensorEventChart(container, {moisture: [...], watering_events: [...]})` against a Plotly mock, and asserts `Plotly.newPlot` was called with a trace whose `x` matches the seeded events' timestamps. This catches the key-name drift cheaply, without needing a browser.

**Better test (real browser):** Same Playwright test as Bug 2 — assert the chart actually renders bars for the seeded watering events.

---

### Bug 4 — Camera-only deployment: Live readings panel rendered as empty header-only box
**Root cause recap:** With no soil sensor / actuators wired, no telemetry rows exist; the `du-stat-grid` had no children.

**Would have been caught by:** No existing e2e test. `test_sense_only_mode_e2e.py` simulates camera-only at the data layer (`pump=no_hardware`, `light=no_hardware`) and asserts the GET response carries the expected health values — but never renders the unit-detail page, so the empty-grid visual regression slipped through.

**Why no test caught it:** This is a JS-rendering posture bug for the brand-new / camera-only state. None of the e2e tests render the unit-detail page in a browser, and no static-HTML integration asserts what the page looks like for a unit with zero telemetry rows.

**Cheapest test to add (no browser):** Extend `test_sense_only_mode_e2e.py::test_e2e_full_first_boot_scenario_then_hardware_added` to GET `/grow/units/<id>` after Phase A (camera-only) and assert specific empty-state copy is in the HTML (e.g. "No live readings yet" or whatever placeholder the fix introduced). This pins the empty-state template contract.

**Better test:** Browser-based snapshot of the unit-detail page in three postures: brand-new (no telemetry, no photos), camera-only (photos but no telemetry), full deployment.

---

### Bug 5 — Photo capture gap 22:00–06:00 silently
**Root cause recap:** `LoopConfig.photo_active_hours` defaulted to `(6, 22)` based on a wrong "no grow light = no useful photo" assumption.

**Would have been caught by:** No existing e2e test. There are unit tests for `photo_active_hours` (`test_api_grow_config.py::test_get_unit_config_includes_photo_active_hours`, `test_config_sync_apply.py::test_apply_config_writes_photo_active_hours_to_loop_config`) — they pin the *plumbing* but not the policy decision (default value). No integration test exercises the safety_loop's photo-capture decision over a 24h simulated clock.

**Why no test caught it:** This is an assumption bug, not a wiring bug. Tests verified config flows end-to-end; they didn't ask "what behaviour does the default produce?" An e2e test would need to simulate the time-of-day branch — i.e. inject a clock at 23:00 and assert no photo is taken, then inject at 12:00 and assert one is.

**Cheapest test to add:** A safety_loop unit test that asserts `LoopConfig().photo_active_hours is None` (the post-fix 24/7 default) — would catch any regression. For e2e, no cheap option exists; the bug was a product-decision regression, not a code-integration regression.

---

### Bug 6 — Default `photo_active_hours = (6, 22)` was never visible in the UI
**Root cause recap:** Operators had no way to discover the schedule — no UI element, no log line, no warning when photos paused.

**Would have been caught by:** No existing e2e test. None of the Configure or unit-detail tests assert that the `photo_active_hours` value appears anywhere on the page. The Configure tab is exercised at the API level (`test_configure_e2e.py`) — every PUT round-trips, but no test asserts the GET-then-render path shows the current value to the operator.

**Why no test caught it:** The bug is a UX visibility gap, not a behavioural defect. Tests verify "if user PUTs X, server stores X and pushes X"; they don't ask "is X presented to the operator anywhere?" That requires either an assertion that the rendered HTML contains the value, or a structured snapshot of the Configure tab DOM.

**Cheapest test to add:** Extend `test_configure_e2e.py` (or a new `unit_detail_render_e2e`) to GET `/grow/units/<id>` after seeding `photo_active_hours` and assert the rendered HTML contains both "Photo schedule" (or whatever the label is) and `06:00` / `22:00`. Pins discoverability.

## Root architectural gap

**There is no test that runs the Plant Grow Unit JS in a real browser — or even in jsdom against a real template render.** The e2e suite's "real" axis is server-side: real Flask app, real WS listener, real DB, real session middleware. But every test asserts on JSON shape or HTML substrings. No test:

1. **Loads `templates/grow_unit_detail.html` and runs the embedded `<script type="module">` against the rendered DOM.** This is the layer where Bugs 2, 3, 4 live (Plotly script tag, JS contract drift with the API, empty-state DOM).
2. **Renders a fleet card from a real `GET /api/grow/units` response.** Bug 1 was a JSON-shape gap that JS reads — the test that would have caught it (assert `last_photo_url` is in the response after a photo lands) is one Flask-test-client GET away from existing in `test_e2e_smoke.py`, but nobody added it.
3. **Asserts on UI postures other than happy-path-with-data.** Bug 4 is the brand-new-camera-only posture; Bug 6 is "I can see what the device thinks the schedule is".

The infrastructure missing is one of:

- **A jsdom-based test harness** (cheapest): import the JS modules, hand them a real fetched response (or a Flask-test-client response), assert on the resulting DOM. There's already a `tests/js/test_grow_card.mjs` file — same pattern just hasn't been extended to other components.
- **A headless browser harness** (Playwright, ~one-time setup cost, much higher fidelity): one fixture that boots Flask + WS listener + a real Chromium, then drives the actual UI. Worth it for the Plotly-loaded assertion which jsdom can't do.
- **Render-and-substring tests** (cheapest of all but lowest signal): hit `/grow/units/<id>` with a Flask test client after seeding state, assert specific strings present/absent in the HTML. Catches Bugs 2 and 6 trivially. Doesn't catch Bug 3 because the JS isn't run.

## Coverage holes by flow

| Flow              | What IS tested                                                                                                        | What is NOT tested                                                                                                                              |
|-------------------|-----------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| **Telemetry**     | One frame in `test_e2e_smoke.py`; happy-path GET shape in unit tests; sense-only health promotion in `sense_only_mode_e2e`. | Zero-row posture (no telemetry rows yet) on the unit-detail page render — Bug 4 lives here.                                                   |
| **Photos**        | One frame in `test_e2e_smoke.py` (write-only — never read back); History `GET /photos/<id>` byte match; cross-unit 404. | The fleet `last_photo_url` field — Bug 1. The unit-detail "latest photo" surface. Photo-active-hours behaviour — Bugs 5, 6.                 |
| **Commands**      | `identify`/`water-now`/`safety_override`/`clear-buffer`/`config_changed` push end-to-end via fake firmware (`configure_e2e`, `phase3_diagnostics_e2e`). | UI-side: does pressing "Water now" actually post the right body? Quick-Controls didn't mount — Bug 3 cascade — and no test catches that.    |
| **Config**        | All Configure PUTs + offline-edit-then-reconnect-pull (`configure_e2e`).                                              | UI rendering of current config values — Bug 6. Default-value sanity (Bug 5).                                                                  |
| **Capabilities**  | All health states + first-boot story at the data layer (`sense_only_mode_e2e`).                                       | UI-side: does the page actually grey out the buttons when health is `no_hardware`?                                                            |
| **Errors / diag** | Diagnostics endpoint contract; PATCH-resolve flow; full observability narrative (`phase3_diagnostics_e2e`).            | UI rendering of errors panel; operator-facing visibility of open errors on the unit-detail page.                                              |
| **Auth / RBAC**   | Real-app authz on every command + Configure endpoint (`grow_authz_e2e`, `configure_e2e` Test 7).                       | RBAC effects on rendered UI (does the viewer actually see read-only chrome?).                                                                 |
| **Postures**      | Happy path with data; admin vs viewer; auth on/off.                                                                   | Brand-new unit (no caps, no telemetry, no photos); camera-only; just-enrolled-not-yet-connected; offline-replay UI; long-disconnect recovery. |

## Recommendations (prioritised)

1. **Extend `test_e2e_smoke.py` to GET the fleet endpoint after the photo upload and assert `last_known_state.last_photo_url` is populated.** ~10 LoC, reuses the existing fixture, would have caught Bug 1 directly. Also add an analogous assertion against `GET /api/grow/units/<id>` to pin the detail-endpoint contract. Highest leverage, lowest cost.

2. **Add a Flask-test-client render assertion for `/grow/units/<id>`.** New small file (or extend `phase3_diagnostics_e2e`): seed a unit, GET `/grow/units/1`, assert `cdn.plot.ly/plotly` substring is present, assert the page contains the unit's label, the canonical empty-state copy when no telemetry/photos exist, and the configured `photo_active_hours` value. Catches Bugs 2, 4, 6 with no new infrastructure. ~30 LoC.

3. **Add a jsdom-based test for `sensor-event-chart.mjs`.** Mirror `tests/js/test_grow_card.mjs`. Mock `Plotly.newPlot`, call `renderSensorEventChart` with a `{moisture, watering_events}` payload (the actual API shape), assert the trace x-values match the seeded event timestamps. Catches Bug 3 directly and locks the contract. ~50 LoC, no new test infrastructure (jsdom already in tests/js/). The jsdom harness should ideally fetch the seed payload from a Flask test client so server/client contract drift fails the test.

4. **Add a "first-deployment posture render" test.** Either a headless-browser test or a server-render substring test that GETs `/grow/units/<id>` for a unit with the camera-only capability set (no telemetry rows) and asserts on the empty-state DOM. This generalises beyond Bug 4 — every "posture" bug (offline replay, brand-new, long-disconnect) can be added as a parameter.

5. **Stand up a Playwright harness with one smoke test.** Boot Flask + WS, drive the unit-detail page, switch to Live tab, assert no `Plotly not loaded` text, assert the chart container contains a Plotly canvas. Pays ongoing dividends — without it, items 2–4 above only catch contract gaps, not actual rendering bugs. Significant one-time infra cost; would have caught Bugs 2, 3, 4 simultaneously and would catch the next class of similar bugs without per-bug effort.

6. **Pin product-policy defaults.** Add a unit test that asserts `LoopConfig().photo_active_hours is None` and that GET `/api/grow/units/<id>/config` for a fresh unit returns `photo_active_hours = null`. Cheap and stops Bug 5 from silently regressing if anyone changes the default again.

7. **Lower priority:** add a posture matrix to `test_phase3_diagnostics_e2e` (brand-new, camera-only, full, offline-recently, offline-long, error-active) where each posture asserts a known-good HTML render. Would catch the long tail of "looks broken on the new unit" bugs without the cost of a browser.

The single highest-ROI change is item 1 (Bug 1 by direct extension) followed by item 2 (Bugs 2, 4, 6 with one new tiny test). Items 3 and 5 together are the only way to systematically fix the "JS contract drifts from API contract" class — Bug 3.
