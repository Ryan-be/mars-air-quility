# Threading Audit — mlss-monitor

Read-only audit of the background threading model on branch
`feature/UI-redesign-using-astrouxds`. The focus is on the Flask + gunicorn
(gthread, 32 threads) worker process after the post-fork hook has restarted
everything.

## 1. Background thread inventory

| Name | Started at | What it does | Cadence | Daemon |
|---|---|---|---|---|
| asyncio driver | `mlss_monitor/app.py:297` (module import) & `gunicorn.conf.py:71` (post_fork) | Runs `thread_loop.run_forever()` so `run_coroutine_threadsafe` works | event-driven | yes |
| `_startup_analysis` | `mlss_monitor/app.py:694` | Calls `run_startup_analysis()` once (hourly+daily backfill). Reads/writes SQLite. | one-shot | yes |
| `_background_log` | `mlss_monitor/app.py:695` | Main data pipeline: reads sensors, writes DB, publishes SSE, runs inference+detection, prunes hot_tier. | every `LOG_INTERVAL` (10s) | yes |
| `_weather_log_loop` | `mlss_monitor/app.py:696` | Fetches Open-Meteo, writes weather_log, publishes forecast events. | 3600s | yes |
| `_sensor_read_loop` | `mlss_monitor/app.py:697` | Reads every DataSource, merges, pushes to hot_tier (DB-backed). | 1s | yes |
| `Timer(_bootstrap)` | `mlss_monitor/app.py:707` | Runs `DetectionEngine.bootstrap_from_db` once, 20s delayed. | one-shot | Timer(non-daemon by default) |
| `pm_sensor_poller` | `sensor_interfaces/sb_components_pm_sensor.py:78` (via `init_pm_sensor` at module import of `app.py`) | Runs blocking PMSA003 UART reads; writes `_cached_result` under `_cache_lock`. | 1s | yes |
| PM executor worker | `sensor_interfaces/sb_components_pm_sensor.py:40` (`ThreadPoolExecutor(max_workers=1)`) | Runs one `_try_read_frame` attempt with a 3s future timeout. | on demand | daemon (default for ThreadPoolExecutor) |
| Flask/gthread workers | gunicorn | Serve HTTP & SSE. 32 per worker. | request-driven | yes |
| Timer fired by `add_inference_tag` → `train_on_tags` | `database/db_logger.py:604` | Only runs inline (no thread); mentioned for completeness. | on POST /tags | n/a |

The gunicorn worker count is 1; all of the above (except gthread HTTP threads) run inside that single worker after `post_fork` re-creates them.

## 2. Per-thread findings

### 2.1 `_start_thread_event_loop` — asyncio driver

- Reads/writes: `state.thread_loop`, `thread_loop` module globals.
- Shares with HTTP threads via `asyncio.run_coroutine_threadsafe` in
  `routes/api_fan.py`, `routes/system.py`, `_background_log`, `_collect_health`.
- **Exception handling**: `run_forever()` swallows most task exceptions, but
  if an exception escapes (or if the thread is killed), it exits silently
  and every subsequent `run_coroutine_threadsafe(...).result()` call will
  hang until its timeout. No watchdog exists.
- **Fork hazard**: handled by post_fork.
- **Finding — Medium**: `routes/api_fan.py:38` calls
  `run_coroutine_threadsafe(...).result()` with **no timeout**. If the
  driver has died (or the Kasa plug is unreachable), the HTTP worker
  thread parks forever. `api_fan.py:52,57` likewise call `.result()`
  with no timeout. Compare `api_fan.py:63` which uses `timeout=5`. Add
  a timeout everywhere.

### 2.2 `_background_log`

- Reads: `state.hot_tier`, `state.feature_vector`, `state.fan_mode`, settings from DB, `fan_controller`.
- **Writes**: `state.feature_vector`, `state.last_auto_action`,
  `state.last_auto_evaluation`, `state.fan_state`, `state.shadow_log`,
  module-level `_log_cycle`, `_last_pm`, `_last_scores_push`.
- **Outer try/except?** There is a `try/except` around `log_data()`
  specifically (`app.py:498-501`), and separate `try/except` blocks
  around each phase. So a single iteration failure is caught. **But
  the `_log_cycle += 1` and the intermediate `time.sleep(LOG_INTERVAL)`
  are both outside any try/except** — if *either* the increment (weird
  but possible if `_log_cycle` ever got corrupted via a monkeypatch) or
  the sleep raised, the thread would die silently. In practice this is
  fine (sleep doesn't raise except on signal), so **Low severity**.
- **Finding — High**: `state.fan_mode`, `state.fan_state`,
  `state.last_auto_action`, `state.last_auto_evaluation` are written by
  this thread and by HTTP handlers in `routes/api_fan.py:28-33,101,112`
  concurrently with **no synchronization**. Under CPython the GIL makes
  individual assignments atomic, but the list construction at
  `app.py:470-473` and the simultaneous read at
  `api_fan.py:113` can observe a torn/outdated list. There's no Lock or
  RLock protecting `state.*`. Fix: use an `RLock` in `state.py` and
  wrap the composite read/write blocks.
- **Finding — Medium**: `state.shadow_log` is a `deque(maxlen=50)`.
  `appendleft` is atomic in CPython, but HTTP readers iterating the
  deque (if any) can observe "deque mutated during iteration". No
  current reader iterates this deque, but the naming ("shadow_log")
  suggests it will be surfaced to an API soon — add a snapshot helper.
- **Finding — Medium**: `asyncio.run_coroutine_threadsafe(...)` at
  `app.py:475-477` is called **without** `.result()` — fire-and-forget.
  If `state.fan_smart_plug.switch(...)` raises, the exception is
  captured on the Future but never observed. Every 10s the fan
  controller fires a new coroutine; if the smart plug is unreachable,
  unhandled exceptions accumulate on the event loop's unfinished-Future
  list. Mitigation: attach a `done_callback` to log failures.
- **Finding — Low**: `_push_anomaly_scores` at `app.py:67-96` reaches
  into `engine._anomaly_detector._last_scores` and `_n_seen` dicts with
  no lock. `AnomalyDetector.learn_and_score` also runs on this same
  thread, so no cross-thread race there — but the `SSE push` reads
  `state.event_bus` which is *thread-safe*, and in theory another
  thread calling `_refresh` on the detector could race. No current
  writer from another thread, so Low.
- **Finding — Medium**: `_last_pm` dict at `app.py:304` is written from
  `read_sensors()` on the log thread and *also* read by HTTP threads
  that go through the same `read_sensors()`? Actually it is only
  touched inside `read_sensors()` which is called only from
  `_background_log`. Safe *today*. If `read_sensors()` ever got called
  from an HTTP handler or from the 1 Hz `_sensor_read_loop`, this
  dict's partial updates would be a race.

### 2.3 `_sensor_read_loop`

- Reads: each `DataSource.get_latest()`, module-level `_data_sources`,
  `hot_tier.push()`.
- **Writes**: `source.last_reading_at` on each DataSource;
  `hot_tier._buffer` (deque, append-only). Also via `hot_tier` into
  SQLite.
- **Outer try/except**: yes, two levels — inside the loop per-source
  and around the whole body. Loop cannot die silently. Good.
- **Fork hazard**: module-level state (`_data_sources`, hot_tier) is
  inherited by fork. HotTier's connection is recreated on first
  failure; acceptable.
- **Finding — Medium**: `hot_tier._buffer` (deque) is appended by this
  thread and **read by the log thread** (`hot_tier.snapshot()` at
  `app.py:512`) and by HTTP handlers. `deque.append` and `list(deque)`
  are GIL-atomic in CPython, but `snapshot()` does `list(self._buffer)`
  on a deque being concurrently appended — this is safe for a
  bounded deque but can raise `RuntimeError: deque mutated during
  iteration` if a reader iterates manually. All current callers use
  `list(...)` which is safe. Keep an eye on it.
- **Finding — High**: HotTier keeps a single persistent sqlite3
  connection opened with `check_same_thread=False`
  (`hot_tier.py:110`). `push()` runs on `_sensor_read_loop`, and
  `prune_old()` runs on `_background_log` — **two different threads
  using the same connection concurrently**. The module docstring
  claims "the GIL keeps single-writer / single-pruner access safe";
  in practice, SQLite with
  `check_same_thread=False` requires the caller to serialise access,
  and concurrent `execute`/`commit` calls from separate threads on
  the same connection can raise `sqlite3.OperationalError: database
  is locked` or (worse, rarely) corrupt the connection state. Even
  under the GIL, `conn.execute` releases the GIL during the C-level
  SQLite call. Fix: wrap all usage in a `threading.Lock()`, or open
  a fresh connection per call (cheap for prune_old, but adds
  overhead for push). Alternative: do prune in the same thread as
  push (the sensor_read_loop).

### 2.4 `_weather_log_loop`

- Only writer to `weather_log`. Has try/except around the body.
- Calls `log_weather()` and `cleanup_old_weather()` each opens its
  own connection, so SQLite thread-affinity is respected. Good.
- **Fork hazard**: `state.open_meteo` is a module-level client.
  Re-usable after fork; `requests` sessions survive fork safely in
  practice.
- No findings.

### 2.5 `Timer(20, _bootstrap)` at `app.py:707`

- **Finding — Medium**: `threading.Timer` inherits `daemon=False` by
  default. If it fires and `bootstrap_from_db` is still running when
  the process receives SIGTERM, `_sys.exit(0)` from the signal
  handler will hang waiting on the Timer thread (non-daemon threads
  block interpreter shutdown). Fix: `t = Timer(...); t.daemon = True;
  t.start()`.
- **Finding — Low**: If `_start_background_services` is called twice
  (e.g. tests), the Timer fires twice and two bootstrap runs compete
  over the same models on disk. The `_services_started` guard prevents
  this in normal use; still, worth making the Timer attribute an
  instance variable so tests can cancel it.

### 2.6 `pm_sensor_poller`

- Dedicated daemon thread owns the blocking UART read. Cache is
  protected by `_cache_lock`. Readers call `get_cached_pm()` which is
  O(1) + a dict copy.
- Outer try/except *is* present inside `_poll_loop` (`pm_sensor.py:96-103`). Good.
- **Fork hazard**: `init_pm_sensor()` runs at `app.py:241` at module
  import time, i.e. in the gunicorn master. That starts both the
  ThreadPoolExecutor and the poller thread — **both are killed by
  fork()**. The `post_fork` hook restarts `_start_background_services`
  but does **not** restart the PM poller or rebuild the executor. In
  production this means after `preload_app=True`:
  - The master's poller is dead.
  - The worker never calls `init_pm_sensor()` again.
  - `read_pm()` (module-level) returns the cache snapshot that was
    seeded in the master before fork. That snapshot is frozen —
    `_cached_monotonic_ts` never advances.
  - `read_sensors()` in `_background_log` sees `pm_fresh = True` on
    the very first post-fork call (because `read_pm()` returns the
    seeded frame) but then every subsequent call **also** returns
    the same frame, with `pm_fresh = True` every time. Because
    `_last_pm["timestamp"]` gets updated to `datetime.utcnow()` each
    cycle, the "cached / stale" fallback path never triggers.
  - The DB is spammed with duplicate PM values; the "staleness"
    never fires.
- **Finding — Critical**: PM poller is not restarted in `post_fork`.
  Add to `gunicorn.conf.py:post_fork`:
  ```python
  from sensor_interfaces import sb_components_pm_sensor as _pm
  if _pm._sensor is not None:
      _pm._sensor._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pm_sensor")
      _pm._sensor._poller_stop = threading.Event()
      _pm._sensor.start_poller(interval=1.0)
  ```
  Or, cleaner: re-run `init_pm_sensor()` inside the worker (idempotent
  if you clear the globals first).
- **Finding — Medium**: `_skip_until` uses `time.monotonic()`; fine.
  `_cached_monotonic_ts` also monotonic. No clock-drift bug. Good.
- **Finding — Medium**: The executor's worker thread is daemon by
  default but never joined on shutdown. `__del__` calls
  `shutdown(wait=False)`. If `__del__` runs during interpreter shutdown
  the Event creation may fail. Minor.

### 2.7 `_services_started` idempotency

- `app.py:668-669`: `Lock + Event`.
- `_start_background_services` locks, checks event, sets event, unlocks
  — then starts threads **outside** the lock. Correct TOCTOU-wise.
- `post_fork` calls `_services_started.clear()` then
  `_start_background_services()`. If two workers were ever forked,
  each has its own copy of the Event (process-local after fork), so
  there's no cross-process race. Good.
- **Finding — Low**: If a test calls `_start_background_services()`
  twice after manually clearing `_services_started`, no cleanup of
  previously-started threads occurs. Not a production issue but a
  test-isolation foot-gun; note for test fixtures.

### 2.8 SQLite concurrency

- `database/db_logger.py:_connect()` opens a new connection per call
  with `timeout=10, busy_timeout=8000, WAL`. Safe for concurrent
  threads (each writes/reads its own connection).
- `hot_tier.py` uses a *shared* connection with
  `check_same_thread=False` — covered above (High).
- **Finding — Low**: `database/db_logger.py:504` (`get_inference_by_id`)
  opens a plain `sqlite3.connect(DB_FILE)` without WAL/busy_timeout.
  Under load this could return `database is locked` more readily than
  the sibling helpers. Tiny inconsistency.
- **Finding — Medium**: `_background_log` writes (`log_sensor_data`,
  `save_inference`, etc.), `_weather_log_loop` writes
  (`log_weather`), and HTTP handlers write (fan settings). All open
  their own connections — OK. WAL mode + 8s busy_timeout is sufficient
  for the observed write rate (~0.1 Hz).

### 2.9 EventBus

- Lock-protected subscriber list + history deque + counter.
- `publish()` calls `sub_queue.put_nowait(msg)` **while holding the
  bus lock**. Queue.put_nowait is fast (no blocking), but if an
  `queue.Full` ever occurs (default Queue is unbounded, so it won't),
  the exception would propagate out with the lock held → deadlock on
  retry. Default queue is unbounded, so Low.
- **Finding — Low**: No hook for subscriber slowness / backpressure.
  A stuck SSE consumer accumulates messages on its queue forever (no
  `maxsize`). Memory leak risk over days. Consider
  `queue.Queue(maxsize=500)` and log/drop when full.
- **Finding — Low**: `publish()` is called from the log thread and
  from HTTP threads (e.g. `save_inference` published from an HTTP
  endpoint). The lock correctly serialises, but note that **none of
  the callers publish from within another lock**, so no deadlock
  risk today — keep it that way.

### 2.10 Shared mutable state in `state.py`

- Many attributes mutated from multiple threads: `fan_mode`,
  `fan_state`, `last_auto_*`, `feature_vector`, `data_source_enabled`,
  `shadow_log`. None are lock-protected.
- **Finding — High**: `state.feature_vector` is assigned by
  `_background_log` (app.py:513) and read by HTTP handlers
  (`routes/api_insights.py:185`) and by other background phases. The
  FeatureVector is a dataclass — whole-object replacement via
  `state.feature_vector = x` is atomic, so readers see either the old
  or the new object, never a torn one. Safe.
- **Finding — Medium**: `state.data_source_enabled` dict is read/
  written without a lock by HTTP (`routes/api_insights.py:391,400`) and
  by the sensor loop indirectly (it doesn't read it today, but any
  future "skip disabled source" check would race). Use a
  `threading.Lock()` or replace with a simple RLock-guarded helper.

### 2.11 Async / asyncio misuse

- `_collect_health` at `app.py:413-416` uses `timeout=3` — good.
- `_background_log`→fan switch at `app.py:475-477` is fire-and-forget
  — see 2.2.
- `routes/api_fan.py:38,52,57` call `.result()` with no timeout — see 2.1.
- `test_async.py` confirms `run_coroutine_threadsafe` works — but there
  is **no test** that `thread_loop` survives the post_fork path (can't
  be tested trivially in a single process; could be simulated by
  pointing `thread_loop` at a closed loop and asserting timeouts).

## 3. Summary of findings

| Severity | Count |
|---|---|
| Critical | 1 |
| High | 3 |
| Medium | 10 |
| Low | 7 |

### Critical
1. **PM poller not restarted in `post_fork`** —
   `gunicorn.conf.py:43-82` + `sensor_interfaces/sb_components_pm_sensor.py`.
   Poller thread is dead in the worker; cache never advances; DB fills
   with duplicates; `pm_stale` never fires.

### Top 5 highest-severity findings
1. *(Critical)* PM poller + executor not restarted in post_fork
   (`gunicorn.conf.py:62-81`).
2. *(High)* HotTier shares a single `sqlite3` connection across
   `_sensor_read_loop` (push) and `_background_log` (prune_old) with no
   lock (`mlss_monitor/hot_tier.py:110, 89-104, 148-166`).
3. *(High)* Unsynchronised shared state: `state.fan_mode`,
   `state.fan_state`, `state.last_auto_action`, `state.last_auto_evaluation`
   written by log thread + HTTP threads without a lock
   (`mlss_monitor/state.py`, `app.py:469-474`, `routes/api_fan.py:28-33`).
4. *(High)* Fire-and-forget `run_coroutine_threadsafe` in log loop at
   `app.py:475-477` — smart-plug errors silently accumulate.
5. *(Medium)* `threading.Timer` at `app.py:707` is non-daemon; can block
   `_sys.exit` during SIGTERM while `bootstrap_from_db` is running.

### Medium list (abbreviated)
- `api_fan.py:38,52,57` — `.result()` with no timeout; hangs on dead
  driver.
- `state.data_source_enabled` dict mutated without a lock
  (`api_insights.py:391,400`).
- `shadow_log` deque has no read-snapshot helper.
- EventBus queues are unbounded — slow SSE consumers leak memory.
- `get_inference_by_id` uses a bare `sqlite3.connect` (no WAL /
  busy_timeout).
- PM executor/poller daemon cleanup at interpreter shutdown.
- Torn-list race on `state.last_auto_evaluation` assignment.

### Low list
- Inconsistent DB connection setup across helpers.
- `_services_started` cleanup on test restart.
- `deque.mutated-during-iteration` risk for `shadow_log` if future
  readers iterate it.
- `_last_pm` dict is single-threaded today; brittle to future refactors.
- `_push_anomaly_scores` touches detector internals without a lock.
- `get_inference_by_id` uses connection without WAL settings.
- Bare exception suppression in `_push_anomaly_scores` hides logic
  bugs.

## 4. Suggested fix summaries

For each finding above, the suggested fix is stated inline. The top
three to act on are:

1. **Restart PM poller in post_fork.** Add the block to
   `gunicorn.conf.py`. Must re-init the executor (broken pipe on
   old file descriptors otherwise) and clear `_poller_stop`.
2. **Serialise HotTier DB access.** Add `self._db_lock =
   threading.Lock()` to `HotTier.__init__` and wrap `_insert_row`,
   `prune_old`, `_load_from_db` bodies.
3. **Add timeouts everywhere `run_coroutine_threadsafe` is awaited.**
   Either a project-wide helper `await_coro(coro, timeout=5)` or an
   audit of the six existing call-sites.
