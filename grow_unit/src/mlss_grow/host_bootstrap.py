"""One-shot legacy-yaml -> /etc/mlss/host migration.

Lives in its own module so the resolver hot path (host_resolver.py)
has no PyYAML import. This module runs once at service startup via
``ensure_host_file()`` and is then dormant for the lifetime of the
process.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mlss_grow.host_resolver import _write_atomically

log = logging.getLogger(__name__)


def ensure_host_file(
    host_file: Path = Path("/etc/mlss/host"),
    legacy_yaml_paths: tuple[Path, ...] = (
        Path("/boot/mlss-grow.yaml"),
        Path("/boot/firmware/mlss-grow.yaml"),
    ),
) -> None:
    """Idempotent migration: if ``host_file`` is missing AND any of the
    ``legacy_yaml_paths`` exists with a ``mlss_host:`` value, copy that
    value across.

    Guarantees:
      - Idempotent: once ``host_file`` exists, this is a no-op.
      - Non-destructive: does not delete the yaml file.
      - Failure-tolerant: corrupt yaml, missing key, file errors -
        all become WARN logs, never crashes.
    """
    if host_file.is_file():
        return                              # already migrated

    import yaml                              # local - only used here

    for yaml_path in legacy_yaml_paths:
        if not yaml_path.is_file():
            continue
        try:
            cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            log.warning(
                "host_bootstrap: %s unreadable (%s), trying next path",
                yaml_path, exc,
            )
            continue
        host = ((cfg or {}).get("mlss_host") or "").strip()
        if not host:
            continue
        _write_atomically(host_file, host, mode=0o664)
        log.info(
            "host_bootstrap: migrated mlss_host=%s from %s -> %s",
            host, yaml_path, host_file,
        )
        return

    log.warning(
        "host_bootstrap: no mlss_host found in yaml and %s missing - "
        "firmware will retry mDNS until an operator writes %s.",
        host_file, host_file,
    )
