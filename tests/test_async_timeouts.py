"""Static-analysis guard: every `<future>.result(...)` call must pass a timeout.

Without a timeout, `concurrent.futures.Future.result()` blocks the caller
forever if the asyncio driver thread has died (or the underlying coroutine
hangs). This test AST-walks `mlss_monitor/` and `external_api_interfaces/`
and fails the build if any new un-timed `.result(...)` sneaks in (H4 in
threading-audit).

If you add a call that is legitimately always-ready (never blocking), add
its `file:lineno` to ``_ALLOWLIST`` below with a justification.
"""
from __future__ import annotations

import ast
import pathlib

# Packages to walk. Kept narrow so test runtime stays negligible.
_ROOTS = ("mlss_monitor", "external_api_interfaces")

# file:lineno -> reason. Keep this empty unless there's a genuinely-unblockable
# .result() call — the whole point of this test is that new ones require a
# conscious decision.
_ALLOWLIST: dict[str, str] = {}


def _iter_py_files() -> list[pathlib.Path]:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    files: list[pathlib.Path] = []
    for root in _ROOTS:
        root_path = repo_root / root
        if root_path.is_dir():
            files.extend(root_path.rglob("*.py"))
    return files


def _call_has_timeout_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "timeout" for kw in call.keywords)


def _is_result_attribute_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "result"
    )


def test_all_result_calls_have_timeout():
    """Every `.result(...)` call in production code must pass `timeout=`.

    Walks every .py file under mlss_monitor/ + external_api_interfaces/,
    flags any `<expr>.result(...)` Call node missing a `timeout` keyword
    argument. Allowlist an entry via `_ALLOWLIST["relpath:lineno"] = reason`
    if (and only if) the call is genuinely never-blocking.
    """
    violations: list[str] = []
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    for path in _iter_py_files():
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if not _is_result_attribute_call(node):
                continue
            # Skip dict.result / dataclass.result — we only care about
            # callables that return Futures. In practice every match will
            # be future.result(); the heuristic here just excludes obvious
            # non-Future attrs by inspecting the receiver expression name
            # where trivial. The strictness of "any .result() without a
            # timeout" is what makes this test cheap and effective.
            key = f"{rel}:{node.lineno}"
            if key in _ALLOWLIST:
                continue
            if not _call_has_timeout_kwarg(node):
                violations.append(key)

    assert not violations, (
        "Found .result() calls without a timeout= kwarg — every such call "
        "can hang forever if the asyncio driver thread has died:\n  "
        + "\n  ".join(violations)
        + "\n\nEither add timeout=<seconds> or, if the call is genuinely "
        "non-blocking, add it to _ALLOWLIST in this test file with a reason."
    )
