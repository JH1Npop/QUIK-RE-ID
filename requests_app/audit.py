"""Activity log (FR-18): who did what, and when, for every action in the portal.

`record(...)` writes one `AuditLog` row. The actor and IP address come from the current web request
(set by `AuditMiddleware`) unless given. Request status changes are copied from `ApprovalLog`
automatically (see `signals.py`), so views only record the other actions: sign-in, passwords,
files opened, and the Admin's changes to accounts, departments and branches.
"""
import contextvars
import logging

logger = logging.getLogger(__name__)
_current_request = contextvars.ContextVar("audit_request", default=None)


class AuditMiddleware:
    """Remembers the current request so `record` can tell who did it and from where."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


def client_ip(request):
    if request is None:
        return None
    # Behind Render's proxy the visitor's address is the LAST one in X-Forwarded-For: the proxy appends what
    # it saw, while anything before it came from the browser and could be made up.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = (forwarded.split(",")[-1] if forwarded else request.META.get("REMOTE_ADDR", "")).strip()
    return ip or None


def record(kind, action, summary, *, actor=None, req=None):
    """Write one activity-log row. Never stops the action itself if logging fails."""
    from .models import AuditLog  # models import this module's neighbours; avoid a cycle at import time

    request = _current_request.get()
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            actor = user
    try:
        AuditLog.objects.create(kind=kind, action=action[:60], summary=summary[:300], actor=actor,
                                request=req, ip=client_ip(request))
    except Exception:  # noqa: BLE001
        logger.exception("Couldn't write the activity log: %s %s", action, summary)
