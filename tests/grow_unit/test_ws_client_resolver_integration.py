"""WS client + host_resolver integration tests.

Mocks both the resolver iterator AND the connect function so we can
drive the WS client's reconnect-loop decision-making without touching
the network.
"""
from unittest.mock import patch, MagicMock

import pytest

from mlss_grow.host_resolver import Candidate, HostUnreachable, Source


@pytest.fixture
def fake_connect():
    """A fake _connect function that records calls and lets the test
    decide which call raises and which succeeds."""
    calls = []
    behaviour: dict = {"raise_at": set(), "ok_value": MagicMock()}
    async def _fake(url, token, cert_path):
        calls.append({"url": url, "token": token, "cert_path": cert_path})
        idx = len(calls) - 1
        if idx in behaviour["raise_at"]:
            raise ConnectionError(f"refused at call {idx}")
        return behaviour["ok_value"]
    return _fake, calls, behaviour


@pytest.mark.asyncio
async def test_ws_client_iterates_until_one_candidate_succeeds(fake_connect):
    fake, _calls, beh = fake_connect
    beh["raise_at"] = {0}     # first candidate fails, second succeeds
    candidates = iter([
        Candidate("192.0.2.10", Source.HOST),
        Candidate("192.0.2.11", Source.CACHE),
    ])
    with patch("mlss_grow.ws_client.hub_candidates", return_value=candidates), \
         patch("mlss_grow.ws_client.record_successful_connect") as mock_rec, \
         patch("mlss_grow.ws_client._default_connect", new=fake):
        from mlss_grow.ws_client import WSClient
        client = WSClient(
            url="wss://placeholder:5001/api/grow/1/ws",
            token="t", buffer_db_path=":memory:",
            on_command=lambda _: None,
        )
        await client._try_connect_once()
    mock_rec.assert_called_once()
    assert mock_rec.call_args.args[0].ip == "192.0.2.11"
    assert mock_rec.call_args.args[0].source == Source.CACHE


@pytest.mark.asyncio
async def test_ws_client_does_not_record_on_failed_handshake(fake_connect):
    fake, _calls, beh = fake_connect
    beh["raise_at"] = {0, 1}      # both candidates fail
    candidates = iter([
        Candidate("192.0.2.10", Source.HOST),
        Candidate("192.0.2.11", Source.CACHE),
    ])
    with patch("mlss_grow.ws_client.hub_candidates", return_value=candidates), \
         patch("mlss_grow.ws_client.record_successful_connect") as mock_rec, \
         patch("mlss_grow.ws_client._default_connect", new=fake):
        from mlss_grow.ws_client import WSClient
        client = WSClient(
            url="wss://placeholder:5001/api/grow/1/ws",
            token="t", buffer_db_path=":memory:",
            on_command=lambda _: None,
        )
        with pytest.raises(HostUnreachable):
            await client._try_connect_once()
    mock_rec.assert_not_called()


@pytest.mark.asyncio
async def test_ws_client_raises_host_unreachable_when_iterator_empty(fake_connect):
    fake, _calls, _beh = fake_connect
    candidates = iter([])
    with patch("mlss_grow.ws_client.hub_candidates", return_value=candidates), \
         patch("mlss_grow.ws_client._default_connect", new=fake):
        from mlss_grow.ws_client import WSClient
        client = WSClient(
            url="wss://placeholder:5001/api/grow/1/ws",
            token="t", buffer_db_path=":memory:",
            on_command=lambda _: None,
        )
        with pytest.raises(HostUnreachable, match="no candidates resolvable"):
            await client._try_connect_once()


@pytest.mark.asyncio
async def test_ws_client_uses_candidate_ip_not_url_host(fake_connect):
    fake, calls, _beh = fake_connect
    candidates = iter([Candidate("192.0.2.10", Source.HOST)])
    with patch("mlss_grow.ws_client.hub_candidates", return_value=candidates), \
         patch("mlss_grow.ws_client.record_successful_connect"), \
         patch("mlss_grow.ws_client._default_connect", new=fake):
        from mlss_grow.ws_client import WSClient
        client = WSClient(
            url="wss://placeholder:5001/api/grow/1/ws",
            token="t", buffer_db_path=":memory:",
            on_command=lambda _: None,
        )
        await client._try_connect_once()
    # URL host substituted with candidate IP
    assert "192.0.2.10" in calls[0]["url"]
