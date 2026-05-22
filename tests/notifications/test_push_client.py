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


# ── Apple APNs BadJwtToken bug (regression suite) ──────────────────────
#
# Apple's APNs gateway rejects JWTs whose ``sub`` claim points at a
# non-routable domain (``localhost``, ``.local``, ``.test`` etc.). The
# old default ``mailto:admin@localhost`` made every Apple push fail with
# ``403 BadJwtToken`` until the operator manually set a contact email.
# The default now uses ``example.com`` (RFC2606 reserved, accepted by
# every push service we've encountered).
def test_empty_contact_email_uses_routable_default_sub():
    """Empty contact email must still produce a sub APNs will accept.

    Apple rejects ``mailto:*@localhost`` with ``BadJwtToken``; we fall
    back to an RFC2606-reserved domain instead.
    """
    sub = {"endpoint": "https://web.push.apple.com/Q4r0SoH4",
           "p256dh": "x", "auth": "y"}
    with patch("mlss_monitor.notifications.push_client._webpush") as mock_wp:
        push_client.send(sub, {"title": "x", "body": "y"},
                         "pubk", "privk", vapid_contact_email="")
    _args, kwargs = mock_wp.call_args
    sub_claim = kwargs["vapid_claims"]["sub"]
    assert sub_claim.startswith("mailto:")
    # Must not be a non-routable TLD APNs rejects.
    for forbidden in ("@localhost", ".local", ".invalid", ".test"):
        assert forbidden not in sub_claim, (
            f"sub={sub_claim!r} contains forbidden token {forbidden!r}; "
            f"Apple APNs will reject this JWT with BadJwtToken"
        )


def test_contact_email_used_when_set():
    """An operator-set contact email is forwarded verbatim as mailto:."""
    sub = {"endpoint": "https://web.push.apple.com/Q4r0SoH4",
           "p256dh": "x", "auth": "y"}
    with patch("mlss_monitor.notifications.push_client._webpush") as mock_wp:
        push_client.send(sub, {"title": "x", "body": "y"},
                         "pubk", "privk",
                         vapid_contact_email="ops@my-pi-hub.example.org")
    _args, kwargs = mock_wp.call_args
    assert kwargs["vapid_claims"]["sub"] == "mailto:ops@my-pi-hub.example.org"


def test_403_bad_jwt_token_is_failed_not_stale():
    """A 403 from APNs (BadJwtToken) is a server-config issue, not a
    per-subscription stale state — fixing the JWT will unblock every
    subscription, so we must not mark single subs stale on 403 alone."""
    sub = {"endpoint": "https://web.push.apple.com/Q4r0SoH4",
           "p256dh": "x", "auth": "y"}
    fake_response = MagicMock(status_code=403,
                              text='{"reason":"BadJwtToken"}')
    from pywebpush import WebPushException
    err = WebPushException("Forbidden", response=fake_response)
    with patch("mlss_monitor.notifications.push_client._webpush", side_effect=err):
        result = push_client.send(sub, {"title": "x", "body": "y"},
                                   "pubk", "privk", "a@b")
    assert result.delivered is False
    # 403 is NOT stale: deleting subs on a per-config error would wipe
    # every device the moment we mis-configure VAPID, including healthy
    # iPhone ones that would work again once the JWT is fixed.
    assert result.stale is False
