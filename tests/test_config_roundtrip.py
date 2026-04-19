"""Contract tests for the dynaconf `envvar_prefix="MLSS"` rule.

Dynaconf is wired in config.py with envvar_prefix="MLSS", meaning only
environment variables starting with `MLSS_` are loaded (the prefix is
stripped on read, so `MLSS_FOO=bar` is read back as `config.get("FOO")`).
These tests enforce that contract and assert `.env.example` conforms.
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
    """An env var without the `MLSS_` prefix must not be readable via `config.get`.

    If dynaconf ever starts returning a value for an unprefixed key, the
    `envvar_prefix="MLSS"` contract in config.py has changed and every
    `config.get()` call site plus `.env.example` need re-auditing.
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

    Enforces that contributors cannot add a new key to .env.example that
    dynaconf would ignore at runtime. Commented example lines are checked
    too, because a future contributor will uncomment them verbatim.

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
