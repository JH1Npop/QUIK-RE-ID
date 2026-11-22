"""Program Head and OSAS screens."""
import calendar
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import roles, services
from .models import ApprovalLog, Department, ReplacementRequest, Status
from .views import show_transition_error

REJECT_NOTIFIED = "Request rejected. The student was notified."


def _with_earlier_flag(qs):
    earlier = ReplacementRequest.objects.filter(
        student=OuterRef("student"), created_at__lt=OuterRef("created_at"),
    )
    return qs.annotate(has_earlier=Exists(earlier))


def _detail_context(req, viewer_role):
    decides_on = Status.PH_APPROVED if viewer_role == roles.OSAS else Status.SUBMITTED
    return {
        "req": req,
        "viewer_role": viewer_role,
        "can_decide": req.status == decides_on,
        "logs": req.with_logs().logs.all(),
        "other_requests": req.student.replacement_requests.exclude(pk=req.pk),
    }


def _reject(request, req, detail_url_name):
    remarks = request.POST.get("remarks", "")  # services refuses an empty reason
    try:
        services.change_status(req, Status.REJECTED, request.user, remarks=remarks)
    except services.TransitionError as e:
        show_transition_error(request, e)
    else:
        messages.success(request, REJECT_NOTIFIED)
    return redirect(detail_url_name, pk=req.pk)


# Program Head ---------------------------------------------------------------

def _ph_request(request, pk):
    req = get_object_or_404(ReplacementRequest.objects.select_related("student__department"), pk=pk)
    if not services.is_program_head_of(request.user, req):
        raise PermissionDenied
    return req


@roles.role_required(roles.PROGRAM_HEAD)
def ph_queue(request):
    mine = ReplacementRequest.objects.filter(student__department__program_head=request.user)
    queue = _with_earlier_flag(mine.filter(status=Status.SUBMITTED).select_related("student__department"))
    recent = mine.exclude(status__in=(Status.SUBMITTED, Status.CANCELLED)).select_related("student").order_by("-updated_at")[:10]
    departments = request.user.headed_departments.all()
    today = timezone.localdate()
    my_week = ApprovalLog.objects.filter(
        actor=request.user, created_at__date__gte=today - timedelta(days=today.weekday()),
    )
    return render(request, "requests_app/review/ph_queue.html", {
        "queue": queue.order_by("updated_at"), "recent": recent, "departments": departments,
        "approved_week": my_week.filter(action=ApprovalLog.Action.PH_APPROVED).count(),
        "rejected_week": my_week.filter(action=ApprovalLog.Action.REJECTED).count(),
    })


@roles.role_required(roles.PROGRAM_HEAD)
def ph_detail(request, pk):
    req = _ph_request(request, pk)
    return render(request, "requests_app/review/detail.html", _detail_context(req, roles.PROGRAM_HEAD))


@require_POST
@roles.role_required(roles.PROGRAM_HEAD)
def ph_approve(request, pk):
    req = _ph_request(request, pk)
    try:
        services.change_status(req, Status.PH_APPROVED, request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
        return redirect("ph_detail", pk=req.pk)
    messages.success(request, "Request approved")
    return redirect("ph_queue")


@require_POST
@roles.role_required(roles.PROGRAM_HEAD)
def ph_reject(request, pk):
    return _reject(request, _ph_request(request, pk), "ph_detail")


# OSAS -----------------------------------------------------------------------

@roles.role_required(roles.OSAS)
def osas_queue(request):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    queue = _with_earlier_flag(
        ReplacementRequest.objects.filter(status=Status.PH_APPROVED).select_related("student__department")
    ).order_by("updated_at")
    this_week = ApprovalLog.objects.filter(created_at__date__gte=week_start)
    approved_this_week = this_week.filter(action=ApprovalLog.Action.OSAS_APPROVED).count()
    # Rejections made by OSAS staff (a Program Head's rejections aren't OSAS's work).
    rejected_this_week = this_week.filter(action=ApprovalLog.Action.REJECTED, actor__groups__name=roles.OSAS).count()
    queue = list(queue)
    q = request.GET.get("q", "").strip()
    found = None
    cancelled_view = request.GET.get("show") == "cancelled"  # the archive of requests students cancelled
    if cancelled_view and not q:
        found = (ReplacementRequest.objects.filter(status=Status.CANCELLED).select_related("student__department")
                 .order_by("-updated_at")[:50])
    if q:
        found = ReplacementRequest.objects.filter(
            Q(reference_no__icontains=q) | Q(student__username__icontains=q) | Q(student__full_name__icontains=q)
        ).select_related("student__department").order_by("-created_at")[:25]
    return render(request, "requests_app/review/osas_queue.html", {
        "queue": queue,
        "waiting": len(queue),
        "late": sum(1 for r in queue if r.days_waiting >= 3),
        "approved_this_week": approved_this_week,
        "rejected_this_week": rejected_this_week,
        "q": q,
        "found": found,
        "cancelled_view": cancelled_view and not q,
        "cancelled_count": ReplacementRequest.objects.filter(status=Status.CANCELLED).count(),
    })


@roles.role_required(roles.OSAS)
def osas_detail(request, pk):
    req = get_object_or_404(ReplacementRequest.objects.select_related("student__department"), pk=pk)
    return render(request, "requests_app/review/detail.html", _detail_context(req, roles.OSAS))


@require_POST
@roles.role_required(roles.OSAS)
def osas_approve(request, pk):
    req = get_object_or_404(ReplacementRequest, pk=pk)
    try:
        services.change_status(req, Status.OSAS_APPROVED, request.user)
    except services.TransitionError as e:
        show_transition_error(request, e)
        return redirect("osas_detail", pk=req.pk)
    messages.success(request, f"Authorization {req.reference_no} created")
    return redirect("osas_queue")


@require_POST
@roles.role_required(roles.OSAS)
def osas_reject(request, pk):
    return _reject(request, get_object_or_404(ReplacementRequest, pk=pk), "osas_detail")


@roles.role_required(roles.OSAS)
def osas_reports(request):
    # Requests per month, last 12 months
    today = timezone.localdate()
    first_month = (today.replace(day=1) - timedelta(days=335)).replace(day=1)
    by_month_raw = dict(
        ReplacementRequest.objects.filter(created_at__date__gte=first_month)
        .annotate(month=TruncMonth("created_at")).values("month")
        .annotate(n=Count("id")).values_list("month", "n")
    )
    by_month_raw = {(m.year, m.month): n for m, n in by_month_raw.items()}
    months = []
    y, m = first_month.year, first_month.month
    for _ in range(12):
        months.append({"label": f"{calendar.month_abbr[m]} {y}", "count": by_month_raw.get((y, m), 0)})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    # Requests per department
    by_dept = list(
        Department.objects.annotate(
            total=Count("members__replacement_requests"),
            released=Count("members__replacement_requests",
                           filter=Q(members__replacement_requests__status=Status.RELEASED)),
            open=Count("members__replacement_requests",
                       filter=~Q(members__replacement_requests__status__in=[Status.RELEASED, Status.CANCELLED])),
        ).values("name", "total", "released", "open").order_by("-total", "name")
    )
    no_dept = ReplacementRequest.objects.filter(student__department__isnull=True).count()

    # Average days from first submission to release
    released = ReplacementRequest.objects.filter(status=Status.RELEASED).annotate(
        released_at=Max("logs__created_at", filter=Q(logs__action=ApprovalLog.Action.RELEASED)),
    ).values_list("created_at", "released_at")
    durations = [(r - c).total_seconds() / 86400 for c, r in released if r]
    avg_days = round(sum(durations) / len(durations), 1) if durations else None

    by_status = {
        row["status"]: row["n"]
        for row in ReplacementRequest.objects.values("status").annotate(n=Count("id"))
    }
    status_rows = [{"code": code, "badge": f"status status-{code.lower().replace('_', '-')}", "label": label,
                    "count": by_status.get(code, 0)} for code, label in Status.choices]

    return render(request, "requests_app/review/reports.html", {
        "months": months,
        "month_max": max([mo["count"] for mo in months] + [1]),
        "by_dept": by_dept,
        "dept_max": max([d["total"] for d in by_dept] + [1]),
        "no_dept": no_dept,
        "avg_days": avg_days,
        "released_count": len(durations),
        "status_rows": status_rows,
        "total": sum(by_status.values()),
        "open_count": sum(n for code, n in by_status.items() if code not in (Status.RELEASED, Status.CANCELLED)),
        "month_total": sum(mo["count"] for mo in months),
    })
