# WS keepalive flapping — code investigation

**Date:** 2026-05-08
**Branch:** `feature/plant-grow-units`
**Trigger:** Anomaly #1 in `2026-05-07-first-deployment-smoke-test.md` — unit "Choclolate habanaro" reconnected ~10× in 1 minute around 21:00 BST.
**Scope:** read-only audit of WS keepalive config on both ends + related liveness paths. No code modified.

---

## What the keepalive config actually is, today

### Firmware side — `grow_unit/src/mlss_grow/ws_client.py`

The `_default_connect` factory at `ws_client.py:60-67` hands these kwargs to `websockets.connect`:

```python
return await websockets.connect(
    url,
    ssl=ctx,
    extra_headers={"Authorization": f"Bearer {token}"},
)
```

That is it. **No `ping_interval`, no `ping_timeout`, no `close_timeout`, no `max_size`** — the call accepts the library defaults. With `websockets ^12.0` (pinned in `grow_unit/pyproject.toml:13`), those defaults are:

| Param          | websockets 12.x default |
|----------------|-------------------------|
| `ping_interval`| **20 s**                |
| `ping_timeout` | **20 s**                |
| `close_timeout`| **10 s**                |

Reconnect loop (`ws_client.py:326-381`): exponential backoff with `backoff_base=1.0`, `backoff_max=60.0`, ±20% jitter (`ws_client.py:332-335`). After a connect succeeds, `attempt` resets to 0, and on a clean disconnect `run_forever` sleeps `backoff_base` (1 s) before the next attempt — i.e. a clean drop is reattempted after **~1 second**, not the backoff curve.

### Server side — `mlss_monitor/routes/api_grow_ws.py`

`websockets.serve(...)` at `api_grow_ws.py:341-345`:

```python
serve_kwargs = {
    "process_request": _process_request,
    "max_size": 8 * 1024 * 1024,  # 8 MB max frame
}
if ssl_context is not None:
    serve_kwargs["ssl"] = ssl_context
srv = await websockets.serve(
    lambda ws, path: _connection_handler(ws, path, registry),
    host, port,
    **serve_kwargs,
)
```

Same story — **no keepalive overrides at all**. Server also takes the library defaults (`ping_interval=20s`, `ping_timeout=20s`).

### Registry — `mlss_monitor/grow/ws_registry.py`

Just a `dict[unit_id, ws]` with a `Lock`. **No application-level heartbeat.** `WSRegistry.send_to_unit` (`ws_registry.py:50-62`) is push-only; no liveness probe, no periodic ping. Liveness is entirely transport-layer (the websockets library's pings).

---

## Both ends ping. They both use the same defaults.

This is the load-bearing observation. With `ping_interval=20s` and `ping_timeout=20s` on both peers and **no offset**:

- Firmware sends a ping every 20 s.
- Server sends a ping every 20 s.
- Either side that doesn't get a pong within 20 s tears the connection down with `1011 keepalive ping timeout`.

On a healthy LAN this is fine. On a Pi Zero W with the BCM43438 1×1 b/g/n radio, two things can happen:

1. **Brief radio gap.** The 43438 is famously prone to ~1-2 s scan-pause / power-save quirks under low signal. If a ping happens to land in that window and the next round-trip takes >20 s to complete (radio re-associating, NDP retransmits…), the websockets library calls it dead. **Both ends will independently decide this**, so the firmware's reconnect handshake races against the server's still-cleaning-up state.

2. **TLS handshake dominates the budget on reconnect.** Pi Zero W is a single-core 1 GHz ARM11 with no AES instructions. `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` with `CERT_REQUIRED + check_hostname=True` (`ws_client.py:39-45`) does the full ECDHE+RSA verify in software. That's typically ~300-700 ms on a Zero W. Not enough to trip the ping timer on its own, but if it overlaps with a buffer-replay storm…

---

## The 21:00 storm — replay self-perpetuation

`_replay_buffer` (`ws_client.py:207-267`) drains every queued row through a sequential `await self._ws.send(row.body)` loop. Each send is fire-and-forget over the same socket the next ping will travel on. A 100k-row buffer (the default cap, `buffer.py:44`) *could* take long enough that an in-flight ping/pong races. But the more realistic scenario for the 21:00 incident: **buffer was small** (the unit had only been online for ~38 m) and the storm came from MLSS being restarted across deploy commits. Each restart:

1. Server WS closes.
2. Firmware sees `ConnectionClosedError`, `_ws=None`, sleep 1 s, reconnect.
3. Replay starts over (whatever the buffer accumulated in those 1-2 s).
4. Server log records "online" → `_record_connection_event(unit_id, "online")` (`api_grow_ws.py:192-228`).
5. Server gets restarted again 60 s later by the next deploy.
6. Repeat.

**That alone explains 10 reconnects in 60 s** — one per deploy push. The keepalive code is probably innocent for *this specific* incident.

---

## Hypothesis-by-hypothesis triage

Ranked from **most likely** to **least likely** given the smoke-test context (overnight session with multiple deploys + a Pi Zero W on flaky WiFi):

### 1. Operator-induced: deploy-time `systemctl restart mlss-monitor`  ★ most likely

- `_record_connection_event` (`api_grow_ws.py:209-221`) writes an `online` row on every accepted upgrade and an `offline` row on every cleanup (`api_grow_ws.py:281`). Restarting MLSS forces a clean close → firmware reconnects in ~1 s → another `online` row.
- The smoke-test doc *explicitly* lists "MLSS service was being restarted as I shipped this overnight session's commits" as a plausible cause.
- **Next-time check:** correlate the 10 `online` timestamps in the `grow_errors` table with `journalctl -u mlss-monitor` restart times. If they line up to within a second, this is the whole story.

### 2. Pi Zero W WiFi flake on BCM43438  ★ likely contributor

- 43438 has known issues with `wpa_supplicant`'s `network={...}` reconnect race + power management. Look for `journalctl --boot --grep "wlan0"` and `dmesg | grep brcmfmac` on the Pi.
- A 1-2 s carrier loss + the 20 s websockets ping timeout = false-dead-peer.
- **Next-time check:** `journalctl -u mlss-grow -f` on the Pi during a flap; if you see `WS connect failed: [Errno 113] No route to host` or `received 1011 (internal error) keepalive ping timeout`, this is the cause. The first means radio dropped before the connect even completed; the second means ping/pong genuinely raced.
- **Likely fix:** `sudo iw dev wlan0 set power_save off` (or via `/etc/NetworkManager/conf.d/` on Bookworm). Or deploy a Pi Zero 2 W which has a BCM43436 — saner driver path.

### 3. websockets ping/pong tighter than RTT  ★ unlikely on a LAN

- Both ends default to 20 s `ping_timeout`. On a healthy LAN even with a Pi Zero W, RTT is single-digit ms. 20 s is luxurious.
- This becomes plausible only if combined with #2 (radio gap absorbs most of the budget) or #5 (replay storm starves the event loop for >20 s).
- **Next-time check:** the journal log for `keepalive ping timeout` *exactly* — that's the websockets-library wording for a true ping race, distinct from `connection closed unexpectedly`.

### 4. systemd watchdog killing the firmware mid-WS  ★ unlikely but worth ruling out

- `mlss-grow.service:15` declares `WatchdogSec=30`.
- `service.py:646-686` — `watchdog_pinger` sends `WATCHDOG=1` every 10 s on a dedicated coroutine via `asyncio.gather(...)` (`service.py:688-693`).
- For systemd to SIGABRT the firmware, that coroutine has to miss 3 consecutive pings — which means the asyncio event loop has been blocked for **>30 s** (some sync blocking call: SD-card write stall, slow camera capture, the SQLite `_evict_if_over_cap` SUM scan on a 100k-row buffer, the seesaw I2C bus wedging on a flaky cable).
- **Next-time check:** `journalctl -u mlss-grow` during a flap. If you see `Watchdog timeout (limit 30s)` or `Killing process ... (SIGABRT)`, this is the cause and the fix is upstream of WS — find what's blocking the loop. If you only see `WS connect failed` / `receive loop ended` lines without an SIGABRT, it isn't this.
- The smoke-test doc reports `uptime 38m through this session` — so the watchdog was *not* firing during that test. Rule this hypothesis #4 not #1, but keep an eye on it once the buffer + photo storage start carrying real load.

### 5. Replay-buffer storm overlapping with ping window  ★ low probability now, real risk later

- `_replay_buffer` (`ws_client.py:207-267`) and `_replay_photos` (`ws_client.py:269-305`) both run sequentially before the receive loop starts. A photo replay of N×JPEGs over the same socket can plausibly take seconds.
- If a replay is mid-flight when a ping is due, the pong is queued behind in-flight `send` calls — and if the link is slow this *can* miss the 20 s budget. This is the self-perpetuating loop the user worried about: replay → drop → reconnect → bigger replay → drop → …
- For the 21:00 incident the buffer was tiny (38 m of camera-only telemetry). Today this is unlikely. **It becomes a real concern once a unit has been offline for 12+ hours** with telemetry plus accumulated photos buffered on disk. The photo buffer is on local SD with no individual size limit beyond the 1 GB cap (`service.py:546-551`).
- **Next-time check:** look for `replaying %d buffered messages` log lines (from `ws_client.py:220`) clustered immediately before disconnects. If the count is large (>1000), the replay is probably implicated.

### 6. Pre-upgrade auth path slow  ★ ruled out

- `_validate_bearer` (`api_grow_ws.py:123-156`) caches Argon2 verifies for 60 s. A reconnect storm hits the cache on every attempt after the first → ~microsecond cost. Auth is *not* the bottleneck here.

---

## What to look at next time the user catches a flap on the Pi

In `journalctl -u mlss-grow -f` (firmware) and `journalctl -u mlss-monitor -f` (server), grouped by what each line means:

| Log line                                                             | Means                                                       | Most likely cause |
|----------------------------------------------------------------------|-------------------------------------------------------------|-------------------|
| `WS connect failed: [Errno 113] No route to host`                    | Couldn't even open the TCP socket                           | Hypothesis 2 (radio) |
| `WS connect failed: [Errno 111] Connection refused`                  | Server isn't listening                                       | Hypothesis 1 (deploy restart) |
| `receive loop ended: ... 1011 (internal error) keepalive ping timeout` | True ping race                                               | Hypothesis 2 or 3 |
| `receive loop ended: ... 1006`                                       | Abnormal close — TCP RST or carrier drop                     | Hypothesis 2 |
| `receive loop ended: ... 1001 (going away)`                          | Peer shut down cleanly                                       | Hypothesis 1 (`systemctl restart`) |
| `Watchdog timeout (limit 30s)` / `process killed (signal SIGABRT)`   | systemd shot us                                              | Hypothesis 4 |
| `replaying N buffered messages` clustered before each disconnect    | Replay self-perpetuation                                     | Hypothesis 5 |

If you see #1006 + the timestamps line up with no MLSS restart in `journalctl -u mlss-monitor`, that's WiFi. Fix is: turn off Pi Zero W WiFi power-save, or upgrade to a Pi Zero 2 W. The keepalive code is likely correct as-is.

---

## File:line reference index

- `grow_unit/src/mlss_grow/ws_client.py:60-67` — `_default_connect`, no keepalive overrides
- `grow_unit/src/mlss_grow/ws_client.py:326-381` — reconnect lifecycle, backoff base/max
- `grow_unit/src/mlss_grow/ws_client.py:207-267` — `_replay_buffer`
- `grow_unit/src/mlss_grow/ws_client.py:269-305` — `_replay_photos`
- `mlss_monitor/routes/api_grow_ws.py:341-345` — `websockets.serve`, no keepalive overrides
- `mlss_monitor/routes/api_grow_ws.py:192-228` — `_record_connection_event` (online/offline rows)
- `mlss_monitor/grow/ws_registry.py` — registry, no app-level heartbeat
- `grow_unit/src/mlss_grow/service.py:646-686` — `watchdog_pinger`, sd_notify every 10 s
- `grow_unit/systemd/mlss-grow.service:13-15` — `Restart=on-failure`, `RestartSec=5`, `WatchdogSec=30`
- `grow_unit/pyproject.toml:13` and root `pyproject.toml:27` — both pin `websockets = "^12.0"`
- `grow_unit/src/mlss_grow/buffer.py:44-45` — buffer caps (100k rows / 50 MB)
