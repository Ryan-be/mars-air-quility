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


import socket


def _socket_getaddrinfo(name: str) -> list[str]:
    """Default DNS resolver. Returns IPv4 addresses first (lower
    latency), then IPv6. Re-raises socket.gaierror on failure so the
    step wrapper can treat it as 'this step yields nothing'."""
    results = socket.getaddrinfo(name, None)
    # results is a list of 5-tuples (family, type, proto, canonname, sockaddr)
    # where sockaddr is (host, port) for AF_INET or (host, port, ...) for AF_INET6.
    v4 = [r[4][0] for r in results if r[0] == socket.AF_INET]
    v6 = [r[4][0] for r in results if r[0] == socket.AF_INET6]
    return v4 + v6


def make_host_step(
    host_file:    Path                          = Path("/etc/mlss/host"),
    dns_resolver: Callable[[str], list[str]]    = _socket_getaddrinfo,
) -> ResolutionStep:
    """Build the Step-1 resolution step (reads /etc/mlss/host).

    IP literals short-circuit DNS entirely (hot path). Hostnames are
    looked up via the injected ``dns_resolver``. All errors -
    file missing, malformed file, DNS failure - yield empty.
    """
    def _step() -> Iterator[Candidate]:
        value = _read_validated(host_file)
        if value is None:
            return
        if _is_ip_literal(value):
            yield Candidate(ip=value, source=Source.HOST)
            return
        try:
            ips = dns_resolver(value)
        except socket.gaierror as exc:
            log.debug("host-step DNS failed for %s: %s", value, exc)
            return
        for ip in ips:
            yield Candidate(ip=ip, source=Source.HOST)
    _step.__name__ = "host_step"
    return _step


def make_cache_step(
    cache_file: Path = Path("/etc/mlss/host-cache"),
) -> ResolutionStep:
    """Build the Step-2 resolution step (reads /etc/mlss/host-cache).

    The cache only ever holds IP literals (the recorder writes the
    resolved IP, never a hostname). If the cache content is anything
    else, treat as missing and yield empty.
    """
    def _step() -> Iterator[Candidate]:
        value = _read_validated(cache_file)
        if value is None:
            return
        if not _is_ip_literal(value):
            log.warning(
                "cache file %s contains non-IP value %r, ignored",
                cache_file, value[:80],
            )
            return
        yield Candidate(ip=value, source=Source.CACHE)
    _step.__name__ = "cache_step"
    return _step


def _zeroconf_resolve(mdns_name: str, timeout_s: float) -> list[str]:
    """Resolve ``mdns_name`` to a list of IPs via python-zeroconf.

    Used only when ``socket.getaddrinfo`` (libnss-mdns path) fails -
    e.g. Avahi's NSS plugin isn't installed or the daemon is wedged.
    Pure Python, no Avahi daemon required.

    Returns a possibly-empty list. Never raises (errors yield empty).
    """
    try:
        from zeroconf import Zeroconf
    except ImportError:
        log.debug("zeroconf not installed; mDNS browse path unavailable")
        return []

    import socket as _socket
    zc = Zeroconf()
    try:
        info = zc.get_service_info(
            "_workstation._tcp.local.",
            mdns_name if mdns_name.endswith(".local.") else mdns_name + ".",
            timeout=int(timeout_s * 1000),  # zeroconf takes ms
        )
        if info is None or not info.addresses:
            return []
        return [_socket.inet_ntoa(addr) for addr in info.addresses if len(addr) == 4]
    finally:
        zc.close()


def make_mdns_step(
    mdns_name:     str                                = "mlss.local",
    dns_resolver:  Callable[[str], list[str]]         = _socket_getaddrinfo,
    mdns_resolver: Callable[[str, float], list[str]]  = _zeroconf_resolve,
    timeout_s:     float                              = 3.0,
) -> ResolutionStep:
    """Build the Step-3 resolution step (mDNS).

    Two sub-paths in priority order:
      1. ``dns_resolver(mdns_name)`` - uses libnss-mdns when Avahi is
         healthy. Fast path.
      2. ``mdns_resolver(mdns_name, timeout_s)`` - pure-Python
         zeroconf browse fallback. Used when libnss-mdns is absent or
         Avahi is wedged.

    Both wrapped in try/except so a wedged path doesn't break the
    other. Yields Candidates marked ``is_authoritative=True`` because
    mDNS reports the hub's *current* address - the resolver knows this
    is the truth, not a cached guess.
    """
    def _step() -> Iterator[Candidate]:
        # Sub-path 1: libnss-mdns via socket.getaddrinfo
        try:
            ips = dns_resolver(mdns_name)
            for ip in ips:
                yield Candidate(ip=ip, source=Source.MDNS, is_authoritative=True)
            if ips:
                return
        except socket.gaierror as exc:
            log.debug("mdns-step libnss path failed: %s", exc)

        # Sub-path 2: zeroconf browse
        try:
            ips = mdns_resolver(mdns_name, timeout_s)
        except Exception as exc:                  # pylint: disable=broad-except
            log.debug("mdns-step zeroconf path failed: %s", exc)
            return
        for ip in ips:
            yield Candidate(ip=ip, source=Source.MDNS, is_authoritative=True)
    _step.__name__ = "mdns_step"
    return _step


DEFAULT_STEPS: tuple[ResolutionStep, ...] = (
    make_host_step(),
    make_cache_step(),
    make_mdns_step(),
)


def hub_candidates(
    steps: tuple[ResolutionStep, ...] = DEFAULT_STEPS,
) -> Iterator[Candidate]:
    """Thin orchestrator. Yields from each step in order. Each step
    handles its own internal errors and yields nothing if it can't
    resolve. An empty iteration means "no candidates" - the iterator
    NEVER raises HostUnreachable itself (the standard Python iterator
    contract is preserved; HostUnreachable is raised by the caller
    when the loop sees no candidates OR when every yielded candidate
    fails its WSS handshake).
    """
    for step in steps:
        try:
            yield from step()
        except Exception as exc:                  # pylint: disable=broad-except
            # A step's internal failure becomes "no candidates from
            # this step". Continue to the next. DEBUG so test runs
            # aren't noisy but production diagnostics work.
            log.debug(
                "resolution step %s failed: %s",
                getattr(step, "__name__", "<step>"), exc,
            )


def record_successful_connect(
    candidate:  Candidate,
    host_file:  Path = Path("/etc/mlss/host"),
    cache_file: Path = Path("/etc/mlss/host-cache"),
) -> None:
    """Persist post-handshake state.

    PRECONDITION (Security Finding 1): caller has completed the full
    WSS handshake including pinned-cert validation AND bearer-token
    auth round-trip. Calling this on a TCP-only success is a bug.

    Effects:
      - Always writes cache_file (last-known-good IP), mode 0600.
      - Updates host_file iff ALL of:
          (a) candidate.is_authoritative is True
          (b) current host_file value is an IP literal (refuses to
              downgrade a hostname like 'mlss.local' silently -
              Security Finding 4)
          (c) the new IP differs from the current
    """
    _write_atomically(cache_file, candidate.ip, mode=0o600)

    if not candidate.is_authoritative:
        return

    current = _read_validated(host_file)
    if current is None or current == candidate.ip:
        return

    if not _is_ip_literal(current):
        log.info(
            "mDNS resolved %s but %s holds a hostname (%s); preserving "
            "operator intent, not self-healing.",
            candidate.ip, host_file, current,
        )
        return

    log.warning(
        "Hub IP changed: %s -> %s (resolved via mDNS). Updating %s so "
        "next boot connects directly.",
        current, candidate.ip, host_file,
    )
    _write_atomically(host_file, candidate.ip, mode=0o664)
