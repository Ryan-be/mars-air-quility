"""Resilient hub-host resolution for grow units.

The WS reconnect loop iterates ``hub_candidates()`` to obtain
``Candidate(ip, source)`` values to try. After a successful WSS
handshake (cert validated + bearer-token round-trip OK), the caller
invokes ``record_successful_connect(candidate)`` to persist the
last-known-good IP and — if the candidate is authoritative AND the
host file currently holds a literal — self-heal ``/etc/mlss/host``.

Design notes (see spec section 5):
  - Strategy pattern: each resolution step is a ``ResolutionStep``
    callable that yields zero or more candidates. ``hub_candidates``
    is a thin orchestrator that ``yield from``s each step in order.
    Adding a new step is one line in ``DEFAULT_STEPS``.
  - The iterator never raises HostUnreachable — that's lifted to the
    caller (preserves the standard Python iterator contract).
  - ``Candidate.is_authoritative`` drives the self-heal decision via a
    boolean flag instead of switching on the source string. The
    resolution step that produces a Candidate decides the policy.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger(__name__)


class Source(str, Enum):
    """Which step produced a given Candidate. ``str`` subclass so log
    formatting via ``"%s" % source`` yields the lowercase value."""
    HOST  = "host"
    CACHE = "cache"
    MDNS  = "mdns"


@dataclass(frozen=True)
class Candidate:
    """One address the WS client should try connecting to.

    ``ip`` is always an IPv4 or IPv6 literal — never a hostname. DNS
    resolution happens *inside* the resolution step; the orchestrator
    never sees hostnames.

    ``is_authoritative`` is True when the producing step's IP source is
    discovery-based (i.e. mDNS), meaning the address is "this is where
    the hub IS right now" rather than "this is where you said the hub
    is." Used by ``record_successful_connect`` to decide whether to
    rewrite /etc/mlss/host.
    """
    ip:               str
    source:           Source
    is_authoritative: bool = False


class HostUnreachable(Exception):
    """The WS client could not connect to any candidate. Raised by the
    WS reconnect loop (not by the resolver iterator) when either
    ``hub_candidates()`` yielded nothing OR every yielded candidate
    failed its WSS handshake."""


# A resolution step takes nothing and yields zero or more candidates.
# Captured state (file paths, timeouts, injected resolvers) is closed
# over via ``functools.partial`` or a small wrapper factory — the public
# interface stays uniform.
ResolutionStep = Callable[[], Iterator[Candidate]]


# RFC 1035 hostname max length is 253 chars. Charset covers DNS names,
# IPv4 literals (digits + dots), and IPv6 literals (hex + colons). We
# include underscore for resilience against hostname schemes that allow
# it, even though strict DNS doesn't.
_HOST_RE = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")


def _read_validated(path: Path) -> str | None:
    """Read a single-line host value from ``path``, enforcing charset
    and length. Returns the stripped value on success, None when the
    file is missing, empty, multi-line, or contains invalid characters.

    Logs a single WARN per invalid file so a repeated failure doesn't
    spam the journal.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        log.warning(
            "host file %s malformed (%d non-blank lines), ignored",
            path, len(lines),
        )
        return None
    value = lines[0]
    if not _HOST_RE.match(value):
        log.warning(
            "host file %s charset/length invalid (%r), ignored",
            path, value[:80],
        )
        return None
    return value


def _write_atomically(path: Path, content: str, mode: int) -> None:
    """Write ``content`` to ``path`` via tmp+rename so a crash mid-write
    cannot leave a torn file. Refuses to write through symlinks —
    ``os.replace`` would otherwise let mlss-grow's group-write on
    /etc/mlss/host turn into a write-where-root-points primitive
    (Security Finding 2).
    """
    if path.is_symlink():
        raise PermissionError(
            f"refusing to write through symlink at {path} - "
            f"resolve with `sudo rm {path}` and restart mlss-grow."
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _is_ip_literal(value: str) -> bool:
    """True iff ``value`` parses as an IPv4 or IPv6 address literal."""
    import ipaddress  # local import - only used in self-heal path
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False
