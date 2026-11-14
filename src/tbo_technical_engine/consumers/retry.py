"""What counts as an outage for this service's event consumers (kit 2.5), the rule kit 1.0 had.

A failure of the infrastructure (the database or Redis restarting, a peer or the token issuer down) leaves the event
pending and it is tried again every `claim_idle_ms`, however long the outage lasts: kit 2.5's own rule (dead-letter an
hour-old entry after 5 tries) is switched off with `transient_max_age=None`, and its narrower classifier is widened
to kit 1.0's: any `OSError` (EHOSTUNREACH, ENETUNREACH, asyncio's "Multiple exceptions ... Connect call failed") and
the token issuer's `UpstreamRefused` also count as an outage. This keeps today's behaviour by the owner's decision.

As on kit 1.0, a deterministic `OSError` is retried for ever too, never dead-lettered; neither loses an event, and
fixing the cause lets the entry through. A bug or a bad event (`ValueError`, `KeyError`) still dead-letters after 5
tries. None of today's handlers calls a peer, so `UpstreamRefused` cannot reach them; it is in the rule so that a
future handler that does keeps kit 1.0's behaviour.

Not given by this: order. See `handlers.py` and `services/readiness.py` for each handler's own guard.
"""

from __future__ import annotations

from tbo_platform_kit.events import is_transient


def is_outage(exc: BaseException) -> bool:
    """The kit's classification, plus kit 1.0's: any `OSError` and the issuer's `UpstreamRefused`."""
    if is_transient(exc) or isinstance(exc, OSError):
        return True
    return any(cls.__name__ == "UpstreamRefused" for cls in type(exc).__mro__)
