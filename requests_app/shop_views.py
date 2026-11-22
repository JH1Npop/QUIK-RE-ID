"""ID shop screen: payment, photo, printing and release."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import audit, roles, services
from django.utils import timezone

from .forms import BranchLocationForm, CalendarForm, ClosureForm, PaymentForm
from .models import AuditLog, BranchClosure, IdShop, ReplacementRequest, Status
from .views import safe_next, show_transition_error

FILTERS = {
    "active": [Status.OSAS_APPROVED, Status.PAID, Status.PHOTO_TAKEN, Status.PRINTED],
    "released": [Status.RELEASED],
    "all": services.SHOP_STATUSES,
}
# The four work stages, in order: what the shop does next for requests in that status.
STAGES = [
    ("pay", Status.OSAS_APPROVED, "To pay"),
    ("photo", Status.PAID, "For photo"),
    ("print", Status.PHOTO_TAKEN, "To print"),
    ("claim", Status.PRINTED, "Ready to claim"),
]
FILTERS.update({key: [status] for key, status, _ in STAGES})


def _shop_request(request, pk):
    req = get_object_or_404(
        ReplacementRequest.objects.select_related("student__department", "shop"),
        pk=pk, status__in=services.SHOP_STATUSES,
    )
    if not services.is_shop_of(request.user, req):
        raise PermissionDenied  # another branch took the payment and finishes this ID
    return req


@roles.role_required(roles.ID_SHOP)
def shop_list(request):
    show = request.GET.get("show", "active")
    if show not in FILTERS:
        show = "active"
    q = request.GET.get("q", "").strip()
    mine = ReplacementRequest.objects.filter(services.shop_scope(request.user))
    reqs = mine.filter(status__in=FILTERS[show]).select_related("student__department", "payment", "shop")
    if q:
        reqs = reqs.filter(Q(reference_no__icontains=q) | Q(student__username__icontains=q)
                           | Q(student__full_name__icontains=q))
    reqs = reqs.order_by("-updated_at" if show in ("released", "all") else "updated_at")
    counts = dict(mine.filter(status__in=FILTERS["active"])
                  .values_list("status").annotate(n=Count("id")))
    today = timezone.localdate()
    visits = (mine.filter(status=Status.OSAS_APPROVED, visit_at__date=today)
              .select_related("student", "shop").order_by("visit_at"))
    return render(request, "requests_app/shop/list.html", {
        "requests": reqs, "q": q, "show": show, "payment_form": PaymentForm(), "visits": visits,
        "shop": request.user.shop, "shops_set_up": IdShop.objects.exists(),
        "next_receipt": services.next_receipt_no(),
        "stages": [{"key": key, "label": label, "count": counts.get(status, 0)} for key, status, label in STAGES],
        "stage_label": next((label for key, _, label in STAGES if key == show), None),
    })


@roles.role_required(roles.ID_SHOP)
def shop_detail(request, pk):
    req = _shop_request(request, pk)
    return render(request, "requests_app/shop/detail.html", {
        "req": req, "logs": req.with_logs().logs.all(), "payment_form": PaymentForm(),
        "next_receipt": services.next_receipt_no(),
    })


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_payment(request, pk):
    req = _shop_request(request, pk)
    back = safe_next(request, reverse("shop_detail", args=[req.pk]))
    form = PaymentForm(request.POST)
    if not form.is_valid():
        errors = [e for errs in form.errors.values() for e in errs]
        messages.error(request, "Payment not recorded. " + " ".join(errors))
        return redirect(back)
    try:
        req = services.record_payment(req, request.user, form.cleaned_data["amount"], form.cleaned_data["receipt_no"] or None)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, "Payment recorded")
        messages.info(request, f"Receipt no. {req.payment.receipt_no}")
    return redirect(back)


def _step(request, pk, new_status, done_message):
    req = _shop_request(request, pk)
    back = safe_next(request, reverse("shop_detail", args=[req.pk]))
    try:
        services.change_status(req, new_status, request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, done_message)
    return redirect(back)


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_photo(request, pk):
    return _step(request, pk, Status.PHOTO_TAKEN, "Status updated")


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_printed(request, pk):
    return _step(request, pk, Status.PRINTED, "Status updated")


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_release(request, pk):
    return _step(request, pk, Status.RELEASED, "ID released")


@roles.role_required(roles.ID_SHOP)
def shop_branch(request):
    """The staffer's own branch: address, hours and map pin, which students see when choosing a branch."""
    shop = request.user.shop
    if shop is None:
        messages.error(request, "You aren't assigned to an ID shop branch yet. Ask the Admin to choose yours.")
        return redirect("shop_list")
    form = BranchLocationForm(request.POST or None, instance=shop)
    if request.method == "POST" and form.is_valid():
        branch = form.save(commit=False)
        branch.location_updated_at = timezone.now()
        branch.save()
        audit.record(AuditLog.Kind.BRANCH, "Updated branch location", branch.name)
        messages.success(request, "Branch location saved")
        return redirect("shop_branch")
    return render(request, "requests_app/shop/branch.html", {"form": form, "shop": shop})


# Visit calendar (FR-16) -------------------------------------------------------

def _own_branch(request):
    shop = request.user.shop
    if shop is None:
        messages.error(request, "You aren't assigned to an ID shop branch yet. Ask the Admin to choose yours.")
    return shop


@roles.role_required(roles.ID_SHOP)
def shop_calendar(request):
    """The branch's visit calendar: weekly hours, slot length, students per slot, booking window, closed dates."""
    shop = _own_branch(request)
    if shop is None:
        return redirect("shop_list")
    form = CalendarForm(request.POST or None, shop=shop)
    if request.method == "POST" and form.is_valid():
        form.save()
        open_days = [name for name, box, _o, _c in form.days() if form.cleaned_data.get(box.name)]
        audit.record(AuditLog.Kind.BRANCH, "Updated visit calendar",
                     f"{shop.name}: {', '.join(open_days) or 'no days'}; {shop.slot_minutes} min slots, "
                     f"{shop.slot_capacity} per slot, {shop.book_days_ahead} days ahead")
        messages.success(request, "Calendar saved")
        outside = services.bookings_outside_hours(shop)
        if outside:
            messages.warning(request, f"{outside} booked visit{'s' if outside != 1 else ''} now fall outside your "
                                      "hours. They stay booked; serve them or ask them to pick a new time.")
        return redirect("shop_calendar")
    return render(request, "requests_app/shop/calendar.html", {
        "shop": shop, "form": form, "closure_form": ClosureForm(),
        "closures": shop.closures.filter(date__gte=timezone.localdate()),
        "preview": services.visit_slots(shop),
    })


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_closure_add(request):
    shop = _own_branch(request)
    if shop is None:
        return redirect("shop_list")
    form = ClosureForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("shop_calendar")
    try:
        cleared = services.add_closure(shop, form.cleaned_data["date"], form.cleaned_data["note"], request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, "Closed date added")
        if cleared:
            messages.info(request, f"{cleared} booked visit{'s were' if cleared != 1 else ' was'} cleared that day. "
                                   "Those students were emailed to pick a new time.")
    return redirect("shop_calendar")


@require_POST
@roles.role_required(roles.ID_SHOP)
def shop_closure_remove(request, pk):
    closure = get_object_or_404(BranchClosure, pk=pk)
    if closure.shop_id != request.user.shop_id:
        raise PermissionDenied
    audit.record(AuditLog.Kind.BRANCH, "Removed closed date", f"{closure.shop.name}: {closure.date:%Y-%m-%d}")
    closure.delete()
    messages.success(request, "Closed date removed")
    return redirect("shop_calendar")
