"""Status changes and everything that must happen with them.

Every change runs in one database transaction that also writes an ApprovalLog
row, and the student is emailed after the transaction commits.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.urls import reverse
from django.utils import timezone

from . import roles
from .models import ApprovalLog, IdShop, Payment, ReplacementRequest, Status, Upload

logger = logging.getLogger(__name__)

# (from, to) -> the role allowed to make that change. Nothing else is possible.
ALLOWED_CHANGES = {
    (Status.SUBMITTED, Status.PH_APPROVED): roles.PROGRAM_HEAD,
    (Status.SUBMITTED, Status.REJECTED): roles.PROGRAM_HEAD,
    (Status.SUBMITTED, Status.CANCELLED): roles.STUDENT,
    (Status.PH_APPROVED, Status.OSAS_APPROVED): roles.OSAS,
    (Status.PH_APPROVED, Status.REJECTED): roles.OSAS,
    (Status.OSAS_APPROVED, Status.PAID): roles.ID_SHOP,
    (Status.PAID, Status.PHOTO_TAKEN): roles.ID_SHOP,
    (Status.PHOTO_TAKEN, Status.PRINTED): roles.ID_SHOP,
    (Status.PRINTED, Status.RELEASED): roles.ID_SHOP,
    (Status.REJECTED, Status.SUBMITTED): roles.STUDENT,
}

SHOP_STATUSES = [Status.OSAS_APPROVED, Status.PAID, Status.PHOTO_TAKEN, Status.PRINTED, Status.RELEASED]


class TransitionError(Exception):
    """A rule stopped the change. The message is safe to show to the user."""


def is_program_head_of(user, req):
    dept = req.student.department
    return dept is not None and dept.program_head_id == user.pk


def is_shop_of(user, req):
    """Whether this ID shop staffer's branch handles the request: the branch the student chose.
    Requests filed before branches existed have no shop and stay open to every branch."""
    return req.shop_id is None or req.shop_id == user.shop_id


def shop_scope(user):
    """Filter for the requests this ID shop staffer's branch handles (see is_shop_of)."""
    q = Q(shop__isnull=True)
    if user.shop_id:
        q |= Q(shop_id=user.shop_id)
    return q


def open_shops():
    """Branches a student can choose: active ones with at least one active ID shop staffer."""
    return (IdShop.objects.filter(is_active=True, staff__is_active=True, staff__groups__name=roles.ID_SHOP)
            .distinct().order_by("name"))


def can_view(user, req):
    """Who may see a request and its uploads: the owner and the staff handling it."""
    if req.student_id == user.pk:
        return True
    if roles.has_role(user, roles.OSAS):
        return True
    if roles.has_role(user, roles.PROGRAM_HEAD) and is_program_head_of(user, req):
        return True
    if roles.has_role(user, roles.ID_SHOP) and req.status in SHOP_STATUSES and is_shop_of(user, req):
        return True
    return False


def _can_act(user, req, role):
    if not roles.has_role(user, role):
        return False
    if role == roles.STUDENT:
        return req.student_id == user.pk
    if role == roles.PROGRAM_HEAD:
        return is_program_head_of(user, req)
    if role == roles.ID_SHOP:
        return is_shop_of(user, req)
    return True


def change_status(req, new_status, actor, *, remarks="", action=None, before_save=None):
    """Move a request to new_status if the rules allow it. Returns the updated request."""
    remarks = (remarks or "").strip()
    with transaction.atomic():
        locked = ReplacementRequest.objects.select_for_update().get(pk=req.pk)
        role = ALLOWED_CHANGES.get((locked.status, new_status))
        if role is None:
            raise TransitionError("That status change isn't allowed.")
        if not _can_act(actor, locked, role):
            raise PermissionDenied
        if new_status == Status.REJECTED and not remarks:
            raise TransitionError("Enter a reason for rejecting the request.")
        if new_status == Status.RELEASED and not Payment.objects.filter(request=locked).exists():
            raise TransitionError("Record a payment before releasing the ID.")
        if before_save:
            before_save(locked)
        locked.status = new_status
        if new_status == Status.REJECTED:
            locked.remarks = remarks
        elif new_status == Status.SUBMITTED:
            locked.remarks = ""
        locked.save()
        ApprovalLog.objects.create(request=locked, actor=actor, action=action or new_status, remarks=remarks)
        transaction.on_commit(lambda: notify_student(locked, remarks))
    return locked


def next_reference_no(year):
    prefix = f"REQ-{year}-"
    last = (
        ReplacementRequest.objects.filter(reference_no__startswith=prefix)
        .order_by(Length("reference_no").desc(), "-reference_no")
        .values_list("reference_no", flat=True)
        .first()
    )
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{number:04d}"


def _save_uploads(req, files):
    for kind, f in files.items():
        if f:
            Upload.objects.create(request=req, kind=kind, file=f)


CLOSED_STATUSES = (Status.RELEASED, Status.CANCELLED)


def open_request(student):
    """The student's request that is still in progress (rejected counts), or None. A student has at most one."""
    return student.replacement_requests.exclude(status__in=CLOSED_STATUSES).order_by("-created_at").first()


def open_request_error(req):
    return TransitionError(f"You already have a request in progress ({req.reference_no}). "
                           "You can file a new one once it's released or cancelled.")


def create_request(student, data, files):
    """Create a SUBMITTED request with its uploads. files maps Upload.Kind -> file.
    Refused while the student still has an open request."""
    if not roles.has_role(student, roles.STUDENT):
        raise PermissionDenied
    year = timezone.localdate().year
    for _attempt in range(5):
        try:
            with transaction.atomic():
                # Lock the student's row so two submits at the same moment can't both pass the check.
                get_user_model().objects.select_for_update().get(pk=student.pk)
                existing = open_request(student)
                if existing:
                    raise open_request_error(existing)
                req = ReplacementRequest.objects.create(
                    reference_no=next_reference_no(year), student=student,
                    reason=data["reason"], date_lost=data["date_lost"], place_lost=data["place_lost"],
                    shop=data.get("shop"),
                )
                _save_uploads(req, files)
                ApprovalLog.objects.create(request=req, actor=student, action=ApprovalLog.Action.SUBMITTED)
                transaction.on_commit(lambda: notify_student(req, ""))
            return req
        except IntegrityError:
            continue  # another request took the same number at the same moment; try the next one
    raise TransitionError("Couldn't create a reference number. Try again.")


def resubmit_request(req, student, data, files):
    """REJECTED -> SUBMITTED with the student's edits and any replaced files."""
    def apply_edits(locked):
        locked.reason = data["reason"]
        locked.date_lost = data["date_lost"]
        locked.place_lost = data["place_lost"]
        if data.get("shop"):
            locked.shop = data["shop"]
        _save_uploads(locked, files)

    return change_status(
        req, Status.SUBMITTED, student, action=ApprovalLog.Action.RESUBMITTED, before_save=apply_edits,
    )


RECEIPT_RE = r"^OR-\d{4}-\d+$"


def next_receipt_no(year=None):
    """The next automatic receipt number, e.g. OR-2026-0001. Typed-in custom numbers don't affect it."""
    year = year or timezone.localdate().year
    prefix = f"OR-{year}-"
    last = (
        Payment.objects.filter(receipt_no__startswith=prefix, receipt_no__regex=RECEIPT_RE)
        .order_by(Length("receipt_no").desc(), "-receipt_no")
        .values_list("receipt_no", flat=True)
        .first()
    )
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{number:04d}"


def record_payment(req, actor, amount, receipt_no=None):
    """OSAS_APPROVED -> PAID, creating the Payment in the same transaction.
    With no receipt_no, the next automatic number is used (retried if another payment took it first)."""
    auto = not receipt_no
    for _attempt in range(5 if auto else 1):
        number = next_receipt_no() if auto else receipt_no

        def create_payment(locked, number=number):
            Payment.objects.create(request=locked, amount=amount, receipt_no=number, recorded_by=actor)
            if locked.shop_id is None:  # filed before branches existed: the paying branch keeps it
                locked.shop_id = actor.shop_id

        try:
            return change_status(
                req, Status.PAID, actor, remarks=f"Receipt {number}, amount {amount}", before_save=create_payment,
            )
        except IntegrityError:
            if not auto:
                raise TransitionError("This receipt number is already recorded.")
    raise TransitionError("Couldn't number the receipt. Try again.")


def notify_student(req, remarks):
    """Email the student the new status and any remarks. A mail failure never breaks the request."""
    student = req.student
    if not student.email:
        return
    lines = [
        f"Hello {student.full_name or student.username},",
        "",
        f"Your ID replacement request {req.reference_no} is now: {req.get_status_display()}.",
    ]
    if remarks:
        lines += ["", f"Remarks: {remarks}"]
    lines += ["", req.next_step_text]
    if req.shop_id and req.is_open:
        shop = req.shop
        lines += [f"ID shop: {shop.name}" + (f", {shop.address}" if shop.address else "")]
    site_url = getattr(settings, "SITE_URL", "")
    if site_url:
        lines += ["", f"View it here: {site_url.rstrip('/')}{reverse('student_detail', args=[req.pk])}"]
        if req.status == Status.OSAS_APPROVED:
            lines += [f"Your authorization for the ID shop: {site_url.rstrip('/')}{reverse('authorization', args=[req.pk])}"]
    lines += ["", "Quik RE-ID"]
    try:
        send_mail(
            f"{req.reference_no}: {req.get_status_display()}",
            "\n".join(lines), None, [student.email],
        )
    except Exception:
        logger.exception("Couldn't email %s about %s", student.email, req.reference_no)
