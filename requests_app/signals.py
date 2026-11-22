"""Feeds the activity log (audit.py): request status changes and sign-in events."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import audit
from .models import ApprovalLog, AuditLog


@receiver(post_save, sender=ApprovalLog)
def copy_status_change(sender, instance, created, **kwargs):
    if not created:
        return
    req = instance.request
    summary = req.reference_no + (f" ({req.student.username})" if req.student_id else "")
    if instance.remarks:
        summary += f": {instance.remarks}"
    audit.record(AuditLog.Kind.REQUEST, instance.get_action_display(), summary, actor=instance.actor, req=req)


@receiver(user_logged_in)
def logged_in(sender, request, user, **kwargs):
    audit.record(AuditLog.Kind.SIGN_IN, "Logged in", user.username, actor=user)


@receiver(user_logged_out)
def logged_out(sender, request, user, **kwargs):
    if user is not None:
        audit.record(AuditLog.Kind.SIGN_IN, "Logged out", user.username, actor=user)


@receiver(user_login_failed)
def login_failed(sender, credentials, request=None, **kwargs):
    audit.record(AuditLog.Kind.SIGN_IN, "Wrong password", str(credentials.get("username", ""))[:150])
