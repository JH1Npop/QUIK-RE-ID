"""Status changes and everything that must happen with them.

Every change runs in one database transaction that also writes an ApprovalLog
row, and the student is emailed after the transaction commits.
"""
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Length
from django.urls import reverse
from django.utils import dateformat, timezone

from . import audit, roles
from .models import ApprovalLog, AuditLog, BranchClosure, IdShop, Payment, ReplacementRequest, Status, Upload

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


# Visit calendar (FR-16) -------------------------------------------------------
# Students book a photo visit at their branch once OSAS approves, until the fee is paid. Booking is optional
# (walk-ins are still served) and isn't a status change, so it doesn't go through change_status.

def _at(day, clock):
    return timezone.make_aware(datetime.combine(day, clock))


NOON = time(12)


def visit_period(start):
    """'Morning' before noon, else 'Afternoon' (local time)."""
    return "Morning" if timezone.localtime(start).time() < NOON else "Afternoon"


def visit_label(start, end=None):
    """'Tue, Nov 25, morning' (+ ' (8:00 AM to 12:00 PM)'), in local time."""
    text = dateformat.format(timezone.localtime(start), "D, M j") + ", " + visit_period(start).lower()
    if end:
        text += (" (" + dateformat.format(timezone.localtime(start), "g:i A") + " to "
                 + dateformat.format(timezone.localtime(end), "g:i A") + ")")
    return text


def day_halves(day, hours):
    """A day's bookable halves from its opening hours, split at noon: [(start, end), ...].
    A branch that opens after noon only has an afternoon; one that closes by noon only a morning."""
    halves = []
    if hours.opens < NOON:
        halves.append((_at(day, hours.opens), _at(day, min(hours.closes, NOON))))
    if hours.closes > NOON:
        halves.append((_at(day, max(hours.opens, NOON)), _at(day, hours.closes)))
    return halves


def branch_takes_bookings(shop):
    return shop is not None and shop.hours_set.exists()


def visit_slots(shop, now=None, exclude=None):
    """The branch's bookable half-days, by day: [{"date", "slots": [{"start", "end", "period", "booked", "left"}]}].

    Students book a morning or an afternoon (FR-16), not an exact time. From today for `book_days_ahead` days
    (today counts), skipping closed dates, days without hours and halves that are already over. A half's load =
    waiting-to-pay requests booked inside [start, end). `exclude` = a request whose own booking doesn't count."""
    now = now or timezone.now()
    hours = {h.weekday: h for h in shop.hours_set.all()}
    if not hours:
        return []
    today = timezone.localtime(now).date()
    last = today + timedelta(days=shop.book_days_ahead - 1)
    closed = set(shop.closures.filter(date__gte=today, date__lte=last).values_list("date", flat=True))
    booked = ReplacementRequest.objects.filter(
        shop=shop, status=Status.OSAS_APPROVED, visit_at__gte=_at(today, time.min),
        visit_at__lt=_at(last + timedelta(days=1), time.min),
    )
    if exclude:
        booked = booked.exclude(pk=exclude)
    booked = list(booked.values_list("visit_at", flat=True))
    days, day = [], today
    while day <= last:
        h = hours.get(day.weekday())
        if h and day not in closed:
            slots = []
            for start, end in day_halves(day, h):
                if end > now:
                    n = sum(1 for b in booked if start <= b < end)
                    slots.append({"start": start, "end": end, "period": visit_period(start), "booked": n,
                                  "left": max(0, shop.slot_capacity - n)})
            if slots:
                days.append({"date": day, "slots": slots})
        day += timedelta(days=1)
    return days


def _email_student(req, subject, lines):
    student = req.student
    if not student.email:
        return
    site_url = getattr(settings, "SITE_URL", "")
    if site_url:
        lines = lines + ["", f"View your request: {site_url.rstrip('/')}{reverse('student_detail', args=[req.pk])}"]
    body = "\n".join([f"Hello {student.full_name or student.username},", ""] + lines + ["", "Quik RE-ID"])

    def send():
        try:
            send_mail(subject, body, None, [student.email])
        except Exception:
            logger.exception("Couldn't email %s about %s", student.email, req.reference_no)
    transaction.on_commit(send)


def book_visit(req, student, start, now=None):
    """Book (or move) the student's visit to the slot starting at `start`. One booking at a time per branch."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = ReplacementRequest.objects.select_for_update().get(pk=req.pk)
        if locked.student_id != student.pk:
            raise PermissionDenied
        if locked.status != Status.OSAS_APPROVED or not locked.shop_id:
            raise TransitionError("You can book a visit after OSAS approves, until the fee is paid.")
        shop = IdShop.objects.select_for_update().get(pk=locked.shop_id)   # serialises bookings at this branch
        slot = next((s for d in visit_slots(shop, now, exclude=locked.pk) for s in d["slots"] if s["start"] == start), None)
        if slot is None:
            raise TransitionError("That time isn't open anymore. Pick another one.")
        if slot["left"] < 1:
            raise TransitionError("That time is full. Pick another one.")
        moved = locked.visit_at is not None
        locked.visit_at = start
        locked.save(update_fields=["visit_at"])
        label = visit_label(slot["start"], slot["end"])
        audit.record(AuditLog.Kind.REQUEST, "Moved visit" if moved else "Booked visit",
                     f"{locked.reference_no}: {label}", actor=student, req=locked)
        _email_student(locked, f"{locked.reference_no}: visit booked", [
            f"Your ID shop visit is booked: {label}, at {shop.name}" + (f", {shop.address}." if shop.address else "."),
            "Bring your authorization slip and the replacement fee. You can change the time on your request page "
            "until you pay.",
        ])
    return locked


def cancel_visit(req, student):
    with transaction.atomic():
        locked = ReplacementRequest.objects.select_for_update().get(pk=req.pk)
        if locked.student_id != student.pk:
            raise PermissionDenied
        if not locked.visit_at or locked.status != Status.OSAS_APPROVED:
            raise TransitionError("There's no booked visit to cancel.")
        label = visit_label(locked.visit_at)
        locked.visit_at = None
        locked.save(update_fields=["visit_at"])
        audit.record(AuditLog.Kind.REQUEST, "Cancelled visit", f"{locked.reference_no}: {label}", actor=student, req=locked)
    return locked


def add_closure(shop, day, note, actor):
    """Close the branch for a day. That day's bookings are cleared and those students are emailed to rebook."""
    with transaction.atomic():
        if BranchClosure.objects.filter(shop=shop, date=day).exists():
            raise TransitionError("That date is already closed.")
        BranchClosure.objects.create(shop=shop, date=day, note=note)
        affected = list(ReplacementRequest.objects.select_for_update().filter(
            shop=shop, status=Status.OSAS_APPROVED,
            visit_at__gte=_at(day, time.min), visit_at__lt=_at(day + timedelta(days=1), time.min)))
        for req in affected:
            label = visit_label(req.visit_at)
            req.visit_at = None
            req.save(update_fields=["visit_at"])
            _email_student(req, f"{req.reference_no}: pick a new visit time", [
                f"{shop.name} is closed on {dateformat.format(day, 'D, M j')}" + (f" ({note})" if note else "")
                + f", so your visit ({label}) was cancelled.",
                "Pick a new time on your request page, or walk in during open hours.",
            ])
        audit.record(AuditLog.Kind.BRANCH, "Added closed date",
                     f"{shop.name}: {day:%Y-%m-%d}" + (f" ({note})" if note else "")
                     + (f"; {len(affected)} booking(s) cleared" if affected else ""), actor=actor)
    return len(affected)


def bookings_outside_hours(shop, now=None):
    """Upcoming bookings that no longer fall in the branch's open hours (after staff change them)."""
    now = now or timezone.now()
    hours = {h.weekday: h for h in shop.hours_set.all()}
    count = 0
    for at in ReplacementRequest.objects.filter(shop=shop, status=Status.OSAS_APPROVED, visit_at__gt=now) \
            .values_list("visit_at", flat=True):
        local = timezone.localtime(at)
        h = hours.get(local.weekday())
        if h is None or not any(start <= at < end for start, end in day_halves(local.date(), h)):
            count += 1
    return count
