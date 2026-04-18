# Threading — Test-Gap Report

Companion to `docs/threading-audit.md`. Catalogs existing concurrency
tests and prioritises the missing ones.

## 1. Existing threading-aware tests

| File | One-line summary |
|---|---|
| `tests/test_async.py` | Verifies `thread_loop` is live in a separate thread, `log_data` dispatches coroutines via `run_coroutine_threadsafe`, `control_fan` blocks on `.result()`, and errors surface as HTTP 500. Does **not** test fork/restart semantics or timeouts. |
| `tests/test_event_bus.py` | Concurrent publisher/consumer test (100 events) and subscribe/unsubscribe-during-publish test (churn ~ 50 publishes, no assertion on correctness — just no deadlock). |
| `tests/test_pm_sensor_poller.py` | Poller populates cache; poller survives exception in `read_pm`; `get_cached_pm(max_age=...)` respects age; module `read_pm()` is non-blocking even if sensor.read_pm sleeps 10s. |
| `tests/test_yaml_io.py::test_concurrent_writes_do_not_corrupt` | 20 threads writing the same YAML file atomically — no errors, file is valid afterwards. |
| `tests/test_sse_integration.py` | Monkeypatches `run_coroutine_threadsafe` for SSE tests. Does not cover backpressure / slow subscribers. |

No test exercises: fork-like state resets, HotTier cross-thread SQLite
access, `_services_started` race, `threading.Timer` behaviour, or
data-source-enabled dict races.

## 2. Missing test coverage — prioritised TODO

### MUST — address before next deploy (cover the Critical / High audit findings)

1. **post_fork hook idempotency + PM poller restart**
   - Property: `_start_background_services()` is idempotent and
     restarts PM poller + asyncio driver from a "post-fork" starting
     state (driver thread dead, Event still set).
   - Test name: `tests/test_post_fork_services.py::test_post_fork_restarts_pm_poller_and_driver`
   - Approach:
     1. Construct the module-level state normally.
     2. Stop the asyncio driver (close the loop), stop the PM poller
        (simulate dead fork).
     3. Replicate `gunicorn.conf.py:post_fork` body inline (new loop,
        new driver thread, clear `_services_started`, call
        `_start_background_services()`, and the *missing* PM restart
        once implemented).
     4. Assert: driver thread is running; `asyncio.run_coroutine_threadsafe`
        returns within 2s; PM poller thread is `is_alive()`; PM cache
        ts advances (monkeypatch `sensor.read_pm` to return a fresh
        frame and wait for the cache to update).
   - Priority: **Must**.

2. **HotTier push/prune concurrency**
   - Property: `HotTier.push` on one thread and `HotTier.prune_old` on
     another do not raise or corrupt the DB.
   - Test name: `tests/test_hot_tier_concurrency.py::test_concurrent_push_and_prune`
   - Approach: `tmp_path` DB, `threading.Barrier(2)` to sync start, run
     N pushes on one thread and N prunes on the other for 1 second,
     collect exceptions into a shared list, assert empty.
   - Also add `test_concurrent_push_from_many_threads` (1 pruner + 4
     pushers) to stress the shared connection.
   - Priority: **Must**.

3. **State mutation race on `state.fan_*`**
   - Property: Under concurrent writes from the log loop and HTTP
     handlers, no thread sees a partial `last_auto_evaluation` (i.e.
     every observed list is either the old or the new, never mixed).
   - Test name: `tests/test_state_race.py::test_fan_state_mutation_is_atomic`
   - Approach: spawn a writer thread that loops assigning alternating
     4-element vs 5-element lists to `state.last_auto_evaluation`, and
     a reader thread doing `len(state.last_auto_evaluation)` in a tight
     loop. Assert that the reader only ever sees 4 or 5, never
     something mixed-in-flight. (Validates the current GIL-atomicity
     assumption; once a lock is added, repurpose to assert
     serialisation.)
   - Priority: **Must**.

4. **`run_coroutine_threadsafe` callers all use timeouts**
   - Property: every `.result()` call in the codebase uses a timeout.
   - Test name: `tests/test_async_timeouts.py::test_all_result_calls_have_timeout`
   - Approach: AST-parse `mlss_monitor/` + `external_api_interfaces/`
     with `ast` module; walk for `Call(func=Attribute(attr='result'))`
     that originate from a `run_coroutine_threadsafe` assignment; flag
     any without a `timeout` keyword. This is a static-analysis test —
     fails the build if someone adds a new un-timed `.result()`.
   - Priority: **Must**.

### SHOULD — cover the remaining Medium findings

5. **`_services_started` Event clear/start race**
   - Property: Two concurrent callers of `_start_background_services`
     result in exactly one set of background threads.
   - Test name: `tests/test_services_idempotent.py::test_concurrent_start_is_idempotent`
   - Approach: monkeypatch `Thread` to a counting stub, clear the Event,
     launch two threads that both call `_start_background_services`
     behind a `threading.Barrier(2)`, count how many stubs were
     constructed — must equal the thread count declared in the function
     (currently 4).
   - Priority: **Should**.

6. **EventBus under concurrent producers + subscribers**
   - Extend `test_event_bus.py` with (a) 4 concurrent publishers + 4
     concurrent subscribers, no dropped events in history; (b) a slow
     subscriber (simulate by never draining its queue) doesn't block
     other subscribers or producers.
   - Test names:
     `tests/test_event_bus.py::test_multiple_producers_and_subscribers`,
     `test_slow_subscriber_does_not_block_publisher`.
   - Approach: `concurrent.futures.ThreadPoolExecutor`, count events
     received. For the slow-sub test, use `queue.Queue(maxsize=0)`
     (unbounded today) and verify memory growth is bounded (flag for
     once backpressure is added).
   - Priority: **Should**.

7. **HotTier reconnect-on-error path**
   - Property: If `_conn` is forcibly closed mid-loop, the next
     `push()` reopens it and the write succeeds.
   - Test name: `tests/test_hot_tier_persistence.py::test_push_recovers_after_conn_closed`
   - Approach: call `hot_tier._conn.close()` then `push(reading)` and
     assert the row is in the DB.
   - Priority: **Should**.

8. **Timer daemon flag at shutdown**
   - Property: `Timer(20, _bootstrap)` created inside
     `_start_background_services` is marked `daemon=True` (so
     `_sys.exit` in the SIGTERM handler doesn't hang on it).
   - Test name: `tests/test_timer_daemon.py::test_bootstrap_timer_is_daemon`
   - Approach: monkeypatch `threading.Timer` to capture the instance,
     call `_start_background_services`, assert the captured instance's
     `.daemon == True`. (Currently fails — Timer defaults to False.)
   - Priority: **Should**.

9. **Background loop resilience to a raising DataSource**
   - Property: `_sensor_read_loop` continues to push readings even if
     one `DataSource.get_latest` raises on every call.
   - Test name: `tests/test_sensor_read_loop.py::test_one_raising_source_does_not_kill_loop`
   - Approach: construct two `DataSource` stubs — one raises, one
     returns a valid reading; run the loop body once (extract the body
     into a helper `_sensor_read_once()` for testability) and assert
     that hot_tier received a merged reading from the surviving
     source.
   - Priority: **Should**. (Will need a minor refactor to expose the
     loop body — flag this when the fix lands.)

10. **Background log loop resilience to a raising event-bus subscriber**
    - Property: If a subscriber's queue somehow raises (e.g. full in a
      future bounded-queue change), `publish()` must not raise out of
      `_background_log`.
    - Test name:
      `tests/test_event_bus.py::test_publish_tolerates_full_subscriber_queue`.
    - Approach: monkeypatch one subscriber's queue to be size=0 and
      verify publish doesn't throw (once bounded). Currently relevant
      only once backpressure is added — mark as NICE until then.
    - Priority: **Should (conditional)**.

### NICE — hardening & future-proofing

11. **data_source_enabled dict race**
    - 8 threads flipping different keys concurrently; assert final
      state is consistent.
    - `tests/test_state_data_source_enabled.py::test_concurrent_flip_is_consistent`.
    - Priority: **Nice**.

12. **shadow_log deque snapshot under concurrent appendleft**
    - Producer thread appendleft-ing, consumer thread snapshotting via
      `list(deque)`; assert no `RuntimeError: deque mutated during
      iteration`.
    - `tests/test_shadow_log.py::test_snapshot_during_appendleft`.
    - Priority: **Nice**.

13. **PM sensor poller: start / stop / restart cycle**
    - Cover the forthcoming restart-in-post_fork change:
      start → stop → start again; assert no zombie thread, cache
      updates resume.
    - Extension to `tests/test_pm_sensor_poller.py`:
      `test_start_stop_restart_cycle`.
    - Priority: **Nice**.

14. **thread_loop exit after fork / unreachable driver**
    - Property: with `thread_loop = asyncio.new_event_loop()` but no
      driver thread, every `run_coroutine_threadsafe(...).result(timeout=2)`
      raises `concurrent.futures.TimeoutError` rather than hanging
      forever.
    - `tests/test_async.py::test_dead_driver_times_out`.
    - Priority: **Nice** (reproduces the bug this whole audit exists
      to prevent regressing).

15. **`_background_log` iteration survives a raising inference engine**
    - Property: If `run_analysis` raises, `_background_log` continues
      to the next iteration (already covered by code structure; a test
      that calls one loop iteration with a monkeypatched `run_analysis`
      that raises would lock this down).
    - `tests/test_background_log.py::test_inference_error_does_not_kill_loop`.
    - Priority: **Nice**.

## 3. Counts

| Priority | Count |
|---|---|
| Must | 4 |
| Should | 6 |
| Nice | 5 |
| **Total** | **15** |

## 4. Top 3 tests to write first

1. `test_post_fork_services.py::test_post_fork_restarts_pm_poller_and_driver`
   — directly exercises the fork class of bug the user is trying to
   prevent regressions of, and would have caught the Critical finding
   in the audit.
2. `test_hot_tier_concurrency.py::test_concurrent_push_and_prune` —
   covers the only shared-connection SQLite call site in the app.
3. `test_async_timeouts.py::test_all_result_calls_have_timeout` — a
   cheap static-analysis guard that prevents future unbounded
   `.result()` hangs like the `api_fan.py` ones flagged in the audit.
