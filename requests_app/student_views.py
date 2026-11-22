from datetime import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import roles, services
from .forms import UPLOAD_ERROR, RequestForm
from .models import ReplacementRequest, Status
from .views import show_transition_error


def _form_context(form, **extra):
    """The request form page, plus the branch list and map data when branches can be chosen."""
    branches = [b.card_data() for b in getattr(form, "shop_choices", [])]
    chosen = form["shop"].value() if "shop" in form.fields else None
    return {"form": form, "branches": branches, "chosen_shop": str(chosen or ""), **extra}


def _own_request(request, pk):
    req = get_object_or_404(ReplacementRequest.objects.select_related("student__department", "shop"), pk=pk)
    if req.student_id != request.user.pk:
        raise PermissionDenied
    return req


def _save_lrn(user, form):
    """Students who signed up before LRN was asked give it on their next request."""
    if form.cleaned_data.get("lrn") and not user.lrn:
        user.lrn = form.cleaned_data["lrn"]
        user.save(update_fields=["lrn"])


@roles.role_required(roles.STUDENT)
def student_list(request):
    reqs = list(request.user.replacement_requests.prefetch_related("logs"))  # each card's tracker reads them
    done = [r for r in reqs if not r.is_open]  # released and cancelled live in the Archived tab
    archived = request.GET.get("show") == "archived"
    status = request.GET.get("status", "")
    filters = {"released": Status.RELEASED, "cancelled": Status.CANCELLED}
    if not archived:
        shown = [r for r in reqs if r.is_open]
    elif status in filters:
        shown = [r for r in done if r.status == filters[status]]
    else:
        status, shown = "", done
    return render(request, "requests_app/student/list.html", {
        "requests": shown, "archived": archived, "status": status, "count_off": len(done),
        "count_released": sum(r.status == Status.RELEASED for r in done),
        "count_cancelled": sum(r.status == Status.CANCELLED for r in done),
        "active": [r for r in reqs if r.is_open],
    })


@roles.role_required(roles.STUDENT)
def student_new(request):
    existing = services.open_request(request.user)
    if existing:
        show_transition_error(request, services.open_request_error(existing))
        return redirect("student_detail", pk=existing.pk)
    first_request = not request.user.replacement_requests.exists()
    form = RequestForm(request.POST or None, request.FILES or None, ask_consent=first_request,
                       ask_lrn=not request.user.lrn, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            _save_lrn(request.user, form)
            try:
                req = services.create_request(request.user, form.cleaned_data, form.upload_files())
            except services.TransitionError as e:
                show_transition_error(request, e)
                existing = services.open_request(request.user)
                if existing:
                    return redirect("student_detail", pk=existing.pk)
            except Exception:  # Cloudinary or network failure while storing the files
                services.logger.exception("Upload failed for %s", request.user.username)
                messages.error(request, UPLOAD_ERROR)
            else:
                messages.success(request, f"Request submitted. Reference no. {req.reference_no}")
                return redirect("student_detail", pk=req.pk)
        elif form.has_upload_error:
            messages.error(request, UPLOAD_ERROR)
    if not request.user.department_id:
        messages.warning(request, "Your account has no department, so no Program Head can review your request. "
                                  "Ask the Admin to set it.")
    return render(request, "requests_app/student/form.html", _form_context(form, is_new=True))


@roles.role_required(roles.STUDENT)
def student_detail(request, pk):
    req = _own_request(request, pk).with_logs()
    visit_days = None
    if req.status == Status.OSAS_APPROVED and services.branch_takes_bookings(req.shop):
        visit_days = services.visit_slots(req.shop, exclude=req.pk)   # the picker (FR-16)
    return render(request, "requests_app/student/detail.html", {
        "req": req, "logs": req.logs.all(), "visit_days": visit_days,
        "visit_label": services.visit_label(req.visit_at, req.visit_end) if req.visit_at else "",
    })


@require_POST
@roles.role_required(roles.STUDENT)
def student_visit(request, pk):
    """Book or move the photo visit at the student's branch."""
    req = _own_request(request, pk)
    try:
        start = datetime.fromisoformat(request.POST.get("slot", ""))   # the picker sends e.g. 2026-11-25T09:00:00+08:00
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
    except ValueError:
        messages.error(request, "Pick a time first.")
        return redirect("student_detail", pk=req.pk)
    try:
        req = services.book_visit(req, request.user, start)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, f"Visit booked: {services.visit_label(req.visit_at, req.visit_end)}")
    return redirect("student_detail", pk=req.pk)


@require_POST
@roles.role_required(roles.STUDENT)
def student_visit_cancel(request, pk):
    req = _own_request(request, pk)
    try:
        services.cancel_visit(req, request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, "Visit cancelled. You can book again or walk in during open hours.")
    return redirect("student_detail", pk=req.pk)


@roles.role_required(roles.STUDENT)
def student_resubmit(request, pk):
    req = _own_request(request, pk)
    if req.status != Status.REJECTED:
        messages.error(request, "Only a rejected request can be resubmitted.")
        return redirect("student_detail", pk=req.pk)
    initial = {"reason": req.reason, "date_lost": req.date_lost, "place_lost": req.place_lost, "shop": req.shop_id}
    form = RequestForm(request.POST or None, request.FILES or None, initial=initial, files_required=False,
                       ask_lrn=not request.user.lrn, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            _save_lrn(request.user, form)
            try:
                services.resubmit_request(req, request.user, form.cleaned_data, form.upload_files())
            except services.TransitionError as e:
                show_transition_error(request, e)
                return redirect("student_detail", pk=req.pk)
            except PermissionDenied:
                raise
            except Exception:
                services.logger.exception("Upload failed for %s", req.reference_no)
                messages.error(request, UPLOAD_ERROR)
            else:
                messages.success(request, "Request resubmitted")
                return redirect("student_detail", pk=req.pk)
        elif form.has_upload_error:
            messages.error(request, UPLOAD_ERROR)
    return render(request, "requests_app/student/form.html", _form_context(form, is_new=False, req=req))


@require_POST
@roles.role_required(roles.STUDENT)
def student_cancel(request, pk):
    req = _own_request(request, pk)
    try:
        services.change_status(req, Status.CANCELLED, request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, "Request cancelled")
    return redirect("student_detail", pk=req.pk)
