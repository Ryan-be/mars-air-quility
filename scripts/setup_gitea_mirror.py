#!/usr/bin/env python3
"""One-shot bootstrap for the Gitea mirror of mars-air-quility.

Run this ONCE from a machine on the same LAN as the Gitea host. The
script is idempotent — repeat-runs skip already-created repos, labels,
projects, and issues.

Prerequisites
-------------
1. Generate a personal access token in Gitea:
     User menu (top-right) → Settings → Applications →
     "Generate New Token" with scopes:
         repo, write:issue, write:repository,
         read:user, read:organization (if using an org)
2. Export it:
     export GITEA_TOKEN=<token>
3. **Required:** export the Gitea instance URL (no default — the LAN IP
   isn't tracked in source, per the test_no_private_ips_committed.py
   privacy guard). Use the RFC 5737 documentation range as a placeholder
   below; substitute your actual LAN IP at run-time:
     export GITEA_URL=http://192.0.2.10:3000     # ← your real Gitea IP
     export GITEA_OWNER=ryan_be
     export GITEA_REPO=mars-air-quility
     export GITHUB_REMOTE=https://github.com/Ryan-be/mars-air-quility.git

What it does
------------
1. Creates the Gitea repo as a PULL MIRROR from GitHub (if missing).
   Gitea then polls GitHub every ``MIRROR_INTERVAL`` and keeps refs
   in sync automatically — devs only push to GitHub, no per-clone
   dual-push wiring needed.
2. If the repo already exists as a *non-mirror* (e.g. created by an
   earlier version of this script), the git-remote dual-push wiring
   is preserved as a fallback so the script still does something
   useful. Pull-mirror repos skip the git-side wiring entirely
   because pushing to a mirror is rejected by Gitea.
3. Creates a canonical set of labels (bug / enhancement / hardware /
   grow-unit / ml / frontend / security / tech-debt / deferred / docs).
4. Creates a "MLSS roadmap" project board with five Kanban columns.
5. Creates one Gitea issue per open backlog entry in
   `docs/Bugs_Improvements_and_Roadmap.md` + four topology follow-ups
   captured here, labels them, and pins them to the Backlog column.

Issues / labels / project board live on the Gitea side only — the
pull mirror only syncs git refs, not Gitea-native metadata. That's
intentional: GitHub stays the canonical code source, Gitea owns the
planning + CI surface.

Re-runs are safe: the script looks up each resource by name first and
only creates what's missing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# GITEA_URL is REQUIRED — no LAN-IP default because that would commit a
# private IP into source. The setup_gitea_mirror.py module raises at
# import time if the env var isn't set (see check below).
GITEA_URL    = os.environ.get("GITEA_URL", "")
GITEA_OWNER  = os.environ.get("GITEA_OWNER",  "ryan_be")
# Repo name on the Gitea side is `MLSS` (the user pre-created it).
# Different from the GitHub side (`mars-air-quility`) on purpose —
# Gitea is the planning + CI side, GitHub stays the public mirror.
GITEA_REPO   = os.environ.get("GITEA_REPO",   "MLSS")
GITHUB_URL   = os.environ.get(
    "GITHUB_REMOTE", "https://github.com/Ryan-be/mars-air-quility.git",
)
# How often Gitea re-polls GitHub for new refs. Go duration string.
# 10m is a sensible default — short enough that local dev feedback
# (push to GH, see refs on Gitea) stays under one tea break, long
# enough that we're not hammering GitHub's clone endpoint.
MIRROR_INTERVAL = os.environ.get("GITEA_MIRROR_INTERVAL", "10m0s")
TOKEN        = os.environ.get("GITEA_TOKEN")


# ─── Labels ────────────────────────────────────────────────────────────────
# Topic labels: what KIND of work the issue covers.
LABELS = [
    ("bug",            "ee0701", "Something is broken in production"),
    ("enhancement",    "84b6eb", "New feature or improvement to existing feature"),
    ("hardware",       "5319e7", "Sensor / actuator / Pi-level hardware work"),
    ("grow-unit",      "0e8a16", "Plant-grow-unit firmware or server-side work"),
    ("ml",             "fbca04", "Detection / attribution / anomaly / inference"),
    ("frontend",       "d4c5f9", "UI / template / static JS / CSS"),
    ("backend",        "ff9f1c", "Flask routes / DB / background workers"),
    ("security",       "b60205", "Auth / TLS / RBAC / secrets / threat-model"),
    ("tech-debt",      "8b4513", "Refactor / cleanup — no user-visible change"),
    ("deferred",       "cccccc", "Captured but not scheduled — re-evaluate later"),
    ("documentation",  "0075ca", "Docs / README / runbook"),
]

# Status labels: where the issue is in the workflow. Gitea 1.22's REST
# API does not expose per-repo project boards (only the web UI does),
# so we use label-based status instead. Same Kanban columns, just
# rendered by filtering on "Labels" in the issue list. The user can
# additionally create a UI project board pointed at these labels if
# they want a drag-and-drop view — that's a one-time click-through,
# not script work.
STATUS_LABELS = [
    ("status:backlog",      "ededed", "Captured, not yet prioritised"),
    ("status:ready",        "c2e0c6", "Prioritised, ready to pick up"),
    ("status:in-progress",  "fbca04", "Actively being worked on"),
    ("status:in-review",    "5319e7", "Open PR / awaiting review"),
    ("status:done",         "0e8a16", "Closed + verified"),
]
DEFAULT_STATUS = "status:backlog"


# ─── Project board notes ───────────────────────────────────────────────────
# Gitea 1.22's per-repo project board feature exists in the web UI but
# has no REST API. We can't automate creating the board or its
# columns. Instead, every issue is tagged with a STATUS_LABELS entry
# (default: status:backlog) — the same Kanban workflow rendered via
# label filtering instead of drag-and-drop.
#
# If you want a UI Kanban view too, do this once manually after the
# script finishes:
#   Repo → Projects → New Project → Board Type: Kanban →
#   Columns: Backlog / Ready / In progress / In review / Done
# Then drag issues from the label-filtered views into the board.
PROJECT_TITLE = "MLSS roadmap"  # Human-readable hint only — not used by the API.


# ─── Issues to import ──────────────────────────────────────────────────────
# Each entry: (title, label-names, body-markdown).
# Bodies are kept terse — they reference the canonical doc instead of
# duplicating the full bug description so the doc remains the source of
# truth and the issue stays grep-able.

ISSUES: list[tuple[str, list[str], str]] = [
    # ── Bugs ──────────────────────────────────────────────────────────────
    (
        "Inference Card Plot UX & Rendering Issues",
        ["bug", "frontend"],
        """\
**Symptoms**
- Plot appears cut off
- Low usefulness / unclear meaning
- Poor scaling and layout
- Minimal or confusing data shown

**Likely root causes** — fixed container height / CSS overflow,
Plotly not resizing correctly, weak data selection (wrong window or
signals), no contextual framing (baseline vs spike).

**Proposed improvements**
- Redefine purpose: Key-Signal Focus / Before-Peak-After view /
  Normalised signals `(value − baseline) / baseline`
- UI fixes: `responsive: true`, `autosize: true`, min-height ~250px,
  axis labels + legend + tooltips
- Add view modes: Raw / Normalised / Single sensor / Multi-sensor
- Event context: vertical event marker, highlight detection window,
  emphasise peak values

Full detail: [`docs/Bugs_Improvements_and_Roadmap.md`](../src/branch/main/docs/Bugs_Improvements_and_Roadmap.md#-bug-inference-card-plot-ux--rendering-issues)
""",
    ),
    (
        "Correlation Plot Scaling & Interpretability",
        ["bug", "frontend"],
        """\
**Symptoms** — sensor values not comparable, large values dominate
(eCO₂ vs PM), hard to see relationships.

**Root cause** — eCO₂ 100–2000+, TVOC 10–100s, PM 1–50 — raw plotting
makes comparison meaningless.

**Recommended fix** — default to normalised (z-score or baseline
ratio) with toggles for Raw / Normalised / % change. Enhance hover
to show raw + transformed values.

**Bonus** — extend existing calc to surface a correlation matrix
over the selected window (strongest relationships + r).

Full detail: [`docs/Bugs_Improvements_and_Roadmap.md`](../src/branch/main/docs/Bugs_Improvements_and_Roadmap.md#-bug-correlation-plot-scaling--interpretability)
""",
    ),
    (
        "PM sensor read-path reliability (MLSS server)",
        ["bug", "hardware", "backend"],
        """\
After fixing the double-poll bug (`e8712db`) and the serial-console
hostage situation, PM data flows but the read path is still noisy.
Three concrete issues observed in production journal:

1. `PM sensor serial error: read failed: [Errno 9] Bad file descriptor`
   between retry attempts — fd closed mid-retry-sequence then
   `read()` called on the closed fd. Doesn't lose data but produces
   avoidable warnings.
2. `device reports readiness to read but returned no data` — classic
   `select()` returning ready but `read()` getting zero bytes.
   Should be a non-fatal partial-frame condition (re-try without
   closing the fd), not a hard error.
3. `'NoneType' object cannot be interpreted as an integer` — parser
   expects a length/checksum byte and gets `None` (partial frame
   where the parser indexes a missing field).

**File**: `sensor_interfaces/sb_components_pm_sensor.py`. Tidy the fd
lifecycle, guard the parser against partial frames, demote the
"device readiness but no data" line from error → debug. Add a unit
test that feeds a truncated frame and asserts the parser returns
`None` cleanly.

**Why this is filed as P2** — data is flowing; these are
quality-of-log + minor robustness gaps, not a data-correctness gap.
""",
    ),

    # ── Plant Grow Unit Phase 4 ───────────────────────────────────────────
    (
        "Plant Grow Unit Phase 4 — Local read-only status UI on the unit",
        ["enhancement", "grow-unit"],
        """\
Tiny Flask app on a separate port (e.g. `http://<pi-ip>:8080/`) so
an operator can SSH-free check a grow unit's health when MLSS is
unreachable.

**Surfaces** (read-only, no actuator controls):
- Live sensor readings
- Buffered-message + buffered-photo counts
- Last successful WS connect time
- Last 50 log lines
- WiFi RSSI

**Trust model** — LAN-only by definition, no auth (same as MLSS).
Actuator controls always route via MLSS so audit/RBAC stays consistent.

**Discovered as a real gap** during the first physical deployment
when the MLSS server's SD card failed mid-deployment and the
operator had no quick way to verify the Pi was still capturing.
""",
    ),

    # ── Plant Grow Unit Phase 5 (smarts) ──────────────────────────────────
    (
        "Plant Grow Unit Phase 5 — Image-based phase classifier",
        ["enhancement", "grow-unit", "ml"],
        """\
Use the per-unit time-lapse photo stream to auto-classify the plant's
current growth phase (seedling / vegetative / flowering / fruiting)
rather than relying on the operator setting it manually.

Hooks into the existing `current_phase` column on `grow_units`
(currently operator-set). Model output would write a derived
`phase_inferred` + confidence; operator override stays authoritative.
""",
    ),
    (
        "Plant Grow Unit Phase 5 — Plant-stage-aware PID adjustments",
        ["enhancement", "grow-unit"],
        """\
The PID watering rules currently use one set of thresholds across
the whole plant lifecycle. Make them phase-aware so e.g. a flowering
chilli gets different soil-moisture targets than the same plant at
the seedling phase.

Today's `grow_plant_profiles` has phase-keyed rows but the firmware
doesn't currently switch behaviour based on `current_phase`. Wire
that switch + add a UI affordance in the Configure tab to preview
the rules per phase.
""",
    ),
    (
        "Plant Grow Unit Phase 5 — Cross-unit anomaly detection",
        ["enhancement", "grow-unit", "ml"],
        """\
With N grow units in the same room, an anomaly on one unit relative
to its siblings is often a more reliable signal than absolute
thresholds (e.g. "all four units' soil moisture dropped 10 % but
unit 3 dropped 30 %" → sensor fault or local condition).

Build a cross-unit baseline comparator that publishes a
`grow_anomaly_detected` event when a single unit deviates >Nσ from
the cohort mean. Hooks into the existing `grow_errors` table for
surfacing.
""",
    ),
    (
        "Plant Grow Unit Phase 5 — Reservoir / water budget tracking",
        ["enhancement", "grow-unit"],
        """\
Surface "how many days of water remain in the reservoir at the
current pulse rate" so the operator can plan refills.

**Inputs needed** — reservoir capacity (config), pulse volume (mL/s,
calibrated per unit), pulses-per-day rolling average from
`grow_watering_events`. **Output** — `days_remaining` field on the
unit card + a notification when <2 days.

Optional hardware: float sensor for direct level readout instead of
the computed estimate.
""",
    ),

    # ── Hardware deferred ─────────────────────────────────────────────────
    (
        "Hardware watchdog (/dev/watchdog) on grow Pi Zero",
        ["hardware", "grow-unit", "deferred"],
        """\
Designed in but **not wired up** due to the risk of a misconfigured
timer rebooting a healthy Pi mid-write.

**Re-evaluate** if a unit silently wedges in production despite the
existing systemd watchdog. Keep this issue open as the index entry —
do NOT close until either implemented or formally rejected.
""",
    ),

    # ── Grow unit hardware additions ──────────────────────────────────────
    (
        "Grow unit hardware: Humidity / air-temperature sensor + VPD",
        ["enhancement", "hardware", "grow-unit"],
        """\
Today the grow unit reports `soil_moisture`, `soil_temp_c`,
`light_state`, `pump_state`, `camera`. Air temperature + RH are
measured on the MLSS server only — useless once a grow unit lives
in a different room from MLSS.

**Adds**
- Per-unit air temperature & humidity tiles
- Vapor Pressure Deficit (VPD) computation — the meaningful
  "is the plant transpiring happily?" metric
- Extend plant-happiness indicator to cover air temp + VPD

**Hardware shortlist** (pick one)
- Adafruit AHT20 (~£5, I2C 0x38) — same chip MLSS already uses,
  driver port near-zero-cost. **Recommended start.**
- Sensirion SHT40/SHT41 (~£10, I2C 0x44) — better long-term drift.
- Bosch BME680 (~£12, I2C 0x77) — also gives pressure + gas/VOC,
  overkill unless we want a CO₂ proxy.

All three share the existing I2C bus (Seesaw soil at 0x36, no
conflict). Wiring documented in `docs/PLANT_GROW_UNIT_HARDWARE.md`.

**Firmware work** (`grow_unit/src/mlss_grow/`)
- New `sensors/aht20.py` mirroring `seesaw.py` — probe at startup,
  expose `read()` → `(temp_c, rh_pct)`, register as a capability
- Extend `service.py` poller to include `air_temp_c` + `air_humidity_pct`
- New `capabilities.py` channel entries

**Server work**
- `mlss_monitor/grow/handlers.py` `_last_known_state`: add new fields
- `database/grow_schema.py` `grow_telemetry`: columns already exist,
  verify WS handler writes them
- `static/js/grow/unit_detail.mjs` CHANNEL_DISPLAY: add tile entries
- Extend plant-happiness threshold columns on `grow_plant_profiles`
  (+8 columns) + seed values per plant × phase

**VPD compute** (derived channel, no extra hardware)
- `SVP_kPa = 0.6108 × exp(17.27 × T / (T + 237.3))`
- `VPD_kPa = SVP × (1 − RH/100)`
- Add `vpd_kpa` server-side in `_last_known_state` (keeps firmware
  contract stable)
- VPD tile + per-plant thresholds: ~0.4–0.8 kPa seedlings,
  ~0.8–1.2 kPa vegetative, ~1.2–1.6 kPa flowering/fruiting,
  >1.6 kPa transpiration stress

**Future extension — LVPD (leaf VPD)**: requires MLX90614 IR thermopile
(~£8, I2C 0x5A) at the canopy. Closer to "true plant happiness" than
air-VPD. Re-evaluate once air-VPD is in production.
""",
    ),

    # ── Security / fleet UX ───────────────────────────────────────────────
    (
        "Fleet-view trust-anchor badge",
        ["enhancement", "security", "frontend"],
        """\
After the CA-publish + `install.sh` rotation-safe update, existing
grow units still pin the **leaf** cert (TOFU) and will break on the
next cert rotation. There's no way to tell at a glance which units
have which trust anchor.

**Proposal** — grow firmware reports its `/etc/mlss/server.crt`
fingerprint (SHA256 truncated to 8 chars) on every WS / capability
handshake. Hub compares against its own `ca.crt` + current leaf
fingerprints and stores a flag on `grow_units`
(`trust_anchor` ∈ `ca` / `leaf` / `unknown`). Fleet card shows a
🔒 CA badge for rotation-safe units, ⚠ leaf otherwise, with hover
tooltip linking to a "re-run install.sh to upgrade" runbook step.

**Effort** ~1 day. New column on `grow_units`, capability protocol
extension (`fingerprint` field), fleet-card pill + tooltip, hub-side
comparison, one new test per side.
""",
    ),

    # ── Future ML direction ───────────────────────────────────────────────
    (
        "Future Direction: Explainable Events",
        ["enhancement", "ml"],
        """\
Combine tagged data + feature vectors + correlation signals to
generate human-readable explanations like:

> "This event was likely caused by cooking because PM2.5 and TVOC
> rose together by 2.3× baseline, matching previous tagged cooking
> events."

Builds on the existing attribution engine + event tagging system.
Output target: a `narrative` field on the inference row that the
dashboard renders as the headline explanation.
""",
    ),

    # ── MLSS Topology follow-ups (called out in PR but deferred) ──────────
    (
        "Topology: AC compressor min-off enforcement",
        ["enhancement", "backend", "tech-debt"],
        """\
TODO marker in `mlss_monitor/effectors/ac.py`. The AC controller
should enforce a 5-minute minimum OFF time after every transition
so the compressor isn't whipsawed by transient temperature blips.

Requires runtime state on the evaluator (timestamp of last
off→on transition per plug) — can't live in `smart_plugs.rules_json`
because it's per-tick state, not config.

Add an in-memory ``state.effector_protection: dict[int, dict]`` keyed
by plug_id, populated by the evaluator's switch path, consulted by
``AC.should_be_on`` before voting ON.
""",
    ),
    (
        "Topology: Carbon-filter fan min-on enforcement",
        ["enhancement", "backend", "tech-debt"],
        """\
Sibling TODO to the AC min-off issue. `FanCarbonFilter` should
enforce a 5-minute minimum ON time so a transient TVOC spike doesn't
flicker the filter media on-off-on.

Same protection-state shape as the AC issue — share the
``state.effector_protection`` dict between both controllers.
""",
    ),
    (
        "Topology: Per-effector schedule editor",
        ["enhancement", "frontend"],
        """\
The side-panel schedule section currently renders a 24-cell grid
read-only with a "coming in v2" marker. Wire actual schedule editing:

- Click any cell to toggle hour ON/OFF
- Persist via `PATCH /api/effectors/<id>` writing `rules.schedule`
- `LightSupplementary.should_be_on` already reads schedule from
  `rules` — no controller-side change needed
- Add a "Apply to all <type>" bulk-copy helper for operators with
  many similar plugs
""",
    ),
    (
        "Topology: Second smart-plug vendor adapter (Shelly / Tuya / Matter)",
        ["enhancement", "backend", "deferred"],
        """\
`SmartPlugProtocol` ABC was reserved during the topology design but
v1 ships Kasa-direct-IP only via
`external_api_interfaces/kasa_smart_plug.py`.

Once a second vendor is acquired:
- Implement the ABC for that vendor in a sibling
  `external_api_interfaces/<vendor>_smart_plug.py`
- Wire dispatch in `mlss_monitor/effectors/store.py` via the
  `smart_plugs.protocol` column (already exists, defaults to `'kasa'`)
- Add the `protocol` field to the add-effector modal's picker
""",
    ),

    # ── Engineering hygiene ───────────────────────────────────────────────
    (
        "Topology: empty /etc/mlss/host self-heal edge case",
        ["bug", "grow-unit"],
        """\
`record_successful_connect` returns early if `current is None`,
meaning a malformed/empty `/etc/mlss/host` blocks mDNS self-heal
from ever filling it in. Empty host file should be a green-light
for mDNS to populate.

Found while testing the resilient-host feature live — minor edge
case, doesn't affect the happy path or the cache rescue.

**File** `grow_unit/src/mlss_grow/host_resolver.py::record_successful_connect`.
**Fix** treat `current is None` AND `current == ""` as "host file
absent — mDNS may write".
""",
    ),
]


# ─── Helpers ───────────────────────────────────────────────────────────────


class GiteaError(RuntimeError):
    """Anything the Gitea API returned that wasn't a 2xx."""


def _api(method: str, path: str, body: Any = None) -> Any:
    """Call the Gitea REST API. ``path`` starts with ``/``; the base
    URL + ``/api/v1`` is prefixed. Returns the parsed JSON body, or
    ``None`` for 204 responses. Raises :class:`GiteaError` for 4xx/5xx.
    """
    url = f"{GITEA_URL.rstrip('/')}/api/v1{path}"
    data = None
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept":        "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise GiteaError(
            f"{method} {path} → HTTP {exc.code}: {body_text}",
        ) from exc


def _git(*args: str) -> str:
    """Run ``git`` in the repo root; return stripped stdout."""
    out = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _check_env() -> None:
    if not GITEA_URL:
        print(
            "ERROR: set GITEA_URL to your Gitea instance, e.g. "
            "`export GITEA_URL=http://<your-gitea-ip>:3000`. There is no "
            "default because the LAN IP is intentionally not tracked in "
            "source (see tests/test_no_private_ips_committed.py).",
            file=sys.stderr,
        )
        sys.exit(2)
    if not TOKEN:
        print(
            "ERROR: set GITEA_TOKEN to a personal access token with "
            "`repo` + `write:issue` + `write:repository` scopes.",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"Gitea  → {GITEA_URL}")
    print(f"Owner  → {GITEA_OWNER}")
    print(f"Repo   → {GITEA_REPO}")
    print(f"GitHub → {GITHUB_URL}")


# ─── Bootstrap steps ───────────────────────────────────────────────────────


def ensure_repo() -> dict:
    """Create the repo as a PULL MIRROR from GitHub if missing; return its dict.

    Gitea's pull-mirror config can only be set at repo-creation time
    (via the ``/repos/migrate`` endpoint with ``mirror: true``) — there
    is no API to convert an existing non-mirror repo into a mirror.
    So this function always creates new repos as mirrors; if one
    already exists in either flavour we return it as-is and leave
    the caller to detect the flavour via ``existing["mirror"]``.

    The migrate endpoint clones the GitHub URL server-side, so no
    git push from the dev machine is needed for refs to land on
    Gitea. From then on Gitea re-polls GitHub every MIRROR_INTERVAL.
    """
    try:
        existing = _api("GET", f"/repos/{GITEA_OWNER}/{GITEA_REPO}")
        kind = "pull mirror" if existing.get("mirror") else "regular repo"
        print(f"✓ Repo exists ({kind}): {existing['full_name']}")
        return existing
    except GiteaError as exc:
        if "404" not in str(exc):
            raise

    # The migrate endpoint creates the repo and triggers the first
    # clone in one call. ``service: github`` tells Gitea to use the
    # GitHub-flavour migrator (handles LFS, releases, etc., though
    # we only care about refs here).
    payload = {
        "clone_addr":      GITHUB_URL,
        "repo_name":       GITEA_REPO,
        "repo_owner":      GITEA_OWNER,
        "mirror":          True,
        "mirror_interval": MIRROR_INTERVAL,
        "private":         False,
        "service":         "github",
        "description":     "MLSS — Mars Life Support Sensor (pull mirror of GitHub)",
        # Pull mirrors disable issues by default. We re-enable so the
        # backlog we're about to import has somewhere to live.
        "issues":          True,
        "wiki":            False,
    }
    created = _api("POST", "/repos/migrate", payload)
    print(
        f"✓ Created pull mirror: {created['full_name']} ← {GITHUB_URL} "
        f"(interval {MIRROR_INTERVAL})",
    )
    return created


def wire_git_remotes(clone_url: str) -> None:
    """Add `gitea` as a remote AND configure dual-push on `origin`.

    The `git remote set-url --add --push` pattern lets a single
    `git push origin <branch>` fan out to multiple URLs. The first
    fetch URL stays GitHub; the push side becomes [GitHub, Gitea].
    """
    remotes = _git("remote").splitlines()
    if "gitea" not in remotes:
        _git("remote", "add", "gitea", clone_url)
        print(f"✓ Added remote: gitea → {clone_url}")
    else:
        _git("remote", "set-url", "gitea", clone_url)
        print(f"✓ Updated remote: gitea → {clone_url}")

    # Reset push URLs on origin so we don't accumulate duplicates on
    # re-runs. The fetch URL is left alone (still GitHub).
    push_urls = _git("remote", "get-url", "--all", "--push", "origin").splitlines()
    needed = {GITHUB_URL, clone_url}
    if set(push_urls) != needed:
        # First --set replaces, subsequent --add appends.
        _git("remote", "set-url", "--push", "origin", GITHUB_URL)
        _git("remote", "set-url", "--add", "--push", "origin", clone_url)
        print(f"✓ Configured dual-push on origin → [{GITHUB_URL}, {clone_url}]")
    else:
        print("✓ Dual-push on origin already configured")


def push_everything() -> None:
    """Push every local branch + every tag to the gitea remote."""
    print("Pushing branches + tags to gitea (this may take a minute)...")
    _git("push", "gitea", "--all")
    _git("push", "gitea", "--tags")
    print("✓ Pushed all branches + tags to gitea")


def ensure_labels(repo_full_name: str) -> dict[str, int]:
    """Create the canonical label set; return {name: id}. Existing
    labels are matched by name (case-insensitive) and reused.

    Iterates both topic labels (LABELS) and status labels
    (STATUS_LABELS) — they're defined separately for readability but
    Gitea sees them as one flat set on the repo.
    """
    existing = {l["name"].lower(): l for l in _api(
        "GET", f"/repos/{repo_full_name}/labels?limit=50",
    )}
    out: dict[str, int] = {}
    for name, color, desc in (*LABELS, *STATUS_LABELS):
        if name.lower() in existing:
            out[name] = existing[name.lower()]["id"]
            continue
        created = _api(
            "POST", f"/repos/{repo_full_name}/labels",
            {"name": name, "color": f"#{color}", "description": desc},
        )
        out[name] = created["id"]
        print(f"  + label: {name}")
    print(f"✓ Labels ready ({len(out)} total)")
    return out


def ensure_issues(
    repo_full_name: str, labels: dict[str, int],
) -> None:
    """Create one issue per ISSUES entry. Skip by title if already
    present. Every new issue gets the default status label
    (status:backlog) on top of its topic labels so the Kanban-by-label
    workflow has the right starting state.
    """
    existing_titles = {
        i["title"] for i in _api(
            "GET",
            f"/repos/{repo_full_name}/issues"
            f"?state=all&type=issues&limit=50",
        )
    }
    default_status_id = labels.get(DEFAULT_STATUS)
    created = skipped = 0
    for title, label_names, body in ISSUES:
        if title in existing_titles:
            skipped += 1
            continue
        label_ids = [labels[n] for n in label_names if n in labels]
        if default_status_id is not None and default_status_id not in label_ids:
            label_ids.append(default_status_id)
        issue = _api(
            "POST", f"/repos/{repo_full_name}/issues",
            {"title": title, "body": body, "labels": label_ids},
        )
        created += 1
        print(f"  + #{issue['number']}: {title}")
    print(f"✓ Issues ready ({created} created, {skipped} skipped)")


def main() -> None:
    _check_env()
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)

    repo = ensure_repo()

    if repo.get("mirror"):
        # Canonical case: repo is a pull mirror. Gitea is fetching
        # refs from GitHub on its own schedule — no local git wiring
        # needed. Trying to ``git push gitea`` here would be rejected
        # ("denying non-fast-forward / mirror is read-only").
        print("✓ Pull mirror — skipping local git remote wiring "
              "(Gitea handles ref sync itself)")
    else:
        # Legacy / fallback path: someone created the repo as a
        # regular non-mirror (e.g. an earlier run of this script
        # before the migrate flip). Keep the dual-push wiring so
        # the repo still gets refs.
        # Construct the clone URL from GITEA_URL ourselves rather than
        # trusting repo["clone_url"]: Gitea returns whatever its
        # `app.ini` `[server] ROOT_URL` is configured to advertise,
        # which may not be the URL we actually reached the API on
        # (stale ROOT_URL after the host moved interfaces).
        clone_url = f"{GITEA_URL.rstrip('/')}/{repo['full_name']}.git"
        wire_git_remotes(clone_url)
        push_everything()

    repo_full_name = repo["full_name"]
    labels = ensure_labels(repo_full_name)
    ensure_issues(repo_full_name, labels)

    print(
        f"\nDone. Browse:\n"
        f"  Repo     → {GITEA_URL}/{repo_full_name}\n"
        f"  Issues   → {GITEA_URL}/{repo_full_name}/issues\n"
        f"  Backlog  → {GITEA_URL}/{repo_full_name}/issues?labels=status:backlog\n"
        f"\nKanban board is label-driven — see PROJECT_TITLE comment in "
        f"this script for optional one-time UI setup.\n",
    )


if __name__ == "__main__":
    main()
