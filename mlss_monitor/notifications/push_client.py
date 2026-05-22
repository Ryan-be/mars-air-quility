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
    vapid_claims = {
        "sub": f"mailto:{vapid_contact_email or 'admin@localhost'}",
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
