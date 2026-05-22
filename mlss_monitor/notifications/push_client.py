"""Thin wrapper around pywebpush.

Translates HTTP failure modes into a tiny SendResult dataclass so the
dispatcher can decide whether to delete the subscription (stale=True)
or just retry next time (stale=False).
"""

import json
import logging
from dataclasses import dataclass

from pywebpush import WebPushException, webpush as _webpush

log = logging.getLogger(__name__)

# Apple's APNs Web Push gateway rejects ``mailto:`` ``sub`` claims that
# point at non-routable domains (``localhost``, ``.local``, ``.test``,
# ``.invalid``) with HTTP 403 ``BadJwtToken``. ``example.com`` is
# RFC2606-reserved and accepted by every Web Push service in the wild.
# Operators are encouraged to set a real contact email via
# :func:`mlss_monitor.notifications.vapid.set_contact_email` so push
# service operators can reach them if their server starts misbehaving.
_DEFAULT_CONTACT_EMAIL = "admin@example.com"


@dataclass(frozen=True)
class SendResult:
    delivered: bool
    stale: bool   # True = endpoint should be deleted (410 Gone)


def send(
    subscription: dict,
    payload: dict,
    vapid_public_key: str,
    vapid_private_key: str,
    vapid_contact_email: str,
) -> SendResult:
    """Send one Web Push. Returns SendResult — never raises."""
    sub_info = {
        "endpoint": subscription["endpoint"],
        "keys": {
            "p256dh": subscription["p256dh"],
            "auth":   subscription["auth"],
        },
    }
    contact = (vapid_contact_email or "").strip() or _DEFAULT_CONTACT_EMAIL
    vapid_claims = {
        "sub": f"mailto:{contact}",
    }
    try:
        _webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
        )
        return SendResult(delivered=True, stale=False)
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 410:
            log.info("Push endpoint gone (410), marking stale: %s",
                     subscription["endpoint"][:60])
            return SendResult(delivered=False, stale=True)
        log.warning("Push failed (status=%s): %s", status, exc)
        return SendResult(delivered=False, stale=False)
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("Push failed (unexpected): %s", exc)
        return SendResult(delivered=False, stale=False)
