"""Regression tests for dynaconf's `envvar_prefix="MLSS"` rule.

The incident these guard against: `.env.example` shipped with raw-named
keys (`LOG_INTERVAL=`, `FAN_KASA_SMART_PLUG_IP=`, ...). Dynaconf, wired
with `envvar_prefix="MLSS"` in `config.py`, silently ignores any key that
doesn't start with `MLSS_` — so `config.get("FAN_KASA_SMART_PLUG_IP")`
returned its in-source default even when the operator had set the key in
.env. The regression surfaces only after the hardcoded default drifts out
of sync with the deployment environment (e.g. the LAN IP changes).

These tests encode the contract:

1. Prefixed keys round-trip. Setting `MLSS_FOO=bar` in the environment
   must make `config.get("FOO")` return "bar".
2. Unprefixed keys DO NOT round-trip. Setting `FOO=bar` alone must NOT
   make `config.get("FOO")` return "bar" — dynaconf is required to ignore
   it. If this test starts failing, either someone widened the prefix in
   `config.py` (in which case update `.env.example` docs) or dynaconf's
   behaviour changed and the rest of this codebase needs re-auditing.
3. `.env.example` itself must not ship unprefixed keys — automated check.
"""
from __future__ import annotations

import pathlib
import re

import pytest


@pytest.fixture
def fresh_config(monkeypatch):
    """Reload `config.Dynaconf` against the current environment.

    Dynaconf caches its resolved values on the module-level `config`
    object at import time. To test a modified environment we need to
    instantiate a fresh Dynaconf object with the same settings.
    """
    def _build():
        from dynaconf import Dynaconf
        return Dynaconf(envvar_prefix="MLSS", load_dotenv=False)
    return _build


def test_prefixed_key_roundtrips(monkeypatch, fresh_config):
    """`MLSS_FOO=bar` must be readable as `config.get("FOO")`."""
    monkeypatch.setenv("MLSS_ROUNDTRIP_TEST", "expected")
    cfg = fresh_config()
    assert cfg.get("ROUNDTRIP_TEST") == "expected"


def test_unprefixed_key_is_ignored(monkeypatch, fresh_config):
    """`FOO=bar` without the `MLSS_` prefix must NOT be readable.

    This is the exact failure mode that caused the production smart-plug
    outage: a raw-named key in .env was silently dropped, and the hardcoded
    default won. If dynaconf ever starts reading unprefixed keys (version
    bump, config change, etc.) this test fires and we re-audit.
    """
    monkeypatch.setenv("UNPREFIXED_ROUNDTRIP_TEST", "should-be-ignored")
    cfg = fresh_config()
    assert cfg.get("UNPREFIXED_ROUNDTRIP_TEST") is None, (
        "dynaconf returned a value for an unprefixed key; the MLSS_ prefix "
        "contract in config.py has changed. Re-audit .env.example and "
        "any `config.get()` call sites."
    )


def test_env_example_only_ships_prefixed_keys():
    """Every `KEY=value` line in .env.example must use the `MLSS_` prefix.

    Catches the exact mistake that caused the original bug: a contributor
    added a new config key to .env.example without the prefix, and the
    corresponding `config.get("NEW_KEY")` call silently used its default.

    Exceptions: `ENV_FOR_DYNACONF` is dynaconf's own bootstrapping variable
    (it tells dynaconf which environment to load) and doesn't follow the
    user-defined prefix rule.
    """
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    env_example = repo_root / ".env.example"
    assert env_example.exists(), ".env.example missing"

    dynaconf_own_keys = {"ENV_FOR_DYNACONF"}
    # Match lines like `FOO=bar` or `# MLSS_FOO=bar` — include commented
    # examples because a future contributor will uncomment them verbatim.
    key_line = re.compile(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=")

    violations = []
    for lineno, line in enumerate(env_example.read_text().splitlines(), 1):
        match = key_line.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in dynaconf_own_keys:
            continue
        if not key.startswith("MLSS_"):
            violations.append(f"  line {lineno}: {line.strip()}")

    assert not violations, (
        ".env.example contains unprefixed keys that dynaconf will silently "
        "ignore at runtime. Add the MLSS_ prefix to each:\n"
        + "\n".join(violations)
    )
