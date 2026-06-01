"""Tests for the pywebpush wrapper + stale endpoint cleanup."""

import json
from unittest.mock import patch, MagicMock

from mlss_monitor.notifications import push_client


def test_send_calls_pywebpush_with_correct_payload():
    sub = {"endpoint": "https://push.example/abc",
           "p256dh": "p256dh-key", "auth": "auth-key"}
    payload = {"title": "Test", "body": "Body", "url": "/x"}
    with patch("mlss_monitor.notifications.push_client._webpush") as mock_wp:
        result = push_client.send(sub, payload,
                                   vapid_public_key="pubk",
                                   vapid_private_key="privk",
                                   vapid_contact_email="a@b")
    assert result.delivered is True
    assert result.stale is False
    _args, kwargs = mock_wp.call_args
    assert kwargs["subscription_info"]["endpoint"] == "https://push.example/abc"
    assert json.loads(kwargs["data"]) == payload
    assert kwargs["vapid_private_key"] == "privk"


def test_410_response_marks_subscription_stale():
    sub = {"endpoint": "https://push.example/gone",
           "p256dh": "x", "auth": "y"}
    fake_response = MagicMock(status_code=410)
    from pywebpush import WebPushException
    err = WebPushException("Gone", response=fake_response)
    with patch("mlss_monitor.notifications.push_client._webpush", side_effect=err):
        result = push_client.send(sub, {"title": "x", "body": "y"},
                                   "pubk", "privk", "a@b")
    assert result.delivered is False
    assert result.stale is True


def test_500_response_marks_failed_but_not_stale():
    sub = {"endpoint": "https://push.example/oops",
           "p256dh": "x", "auth": "y"}
    fake_response = MagicMock(status_code=500)
    from pywebpush import WebPushException
    err = WebPushException("Internal", response=fake_response)
    with patch("mlss_monitor.notifications.push_client._webpush", side_effect=err):
        result = push_client.send(sub, {"title": "x", "body": "y"},
                                   "pubk", "privk", "a@b")
    assert result.delivered is False
    assert result.stale is False


def test_arbitrary_exception_marks_failed_not_stale():
    sub = {"endpoint": "https://push.example/oops",
           "p256dh": "x", "auth": "y"}
    with patch("mlss_monitor.notifications.push_client._webpush",
               side_effect=RuntimeError("network down")):
        result = push_client.send(sub, {"title": "x", "body": "y"},
                                   "pubk", "privk", "a@b")
    assert result.delivered is False
    assert result.stale is False
