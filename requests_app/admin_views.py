"""Admin screens: accounts (create, edit, deactivate, reactivate), departments (add, rename, Program Head)
and ID shop branches (add, rename). Departments and branches can be archived (deactivated) and reactivated."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import roles
from .forms import AccountEditForm, AssignHeadForm, DepartmentForm, IdShopForm, StaffCreateForm
from .models import Department, IdShop, ReplacementRequest, Status, User

CLOSED = (Status.RELEASED, Status.CANCELLED)


@roles.role_required(roles.ADMIN)
def manage_users(request):
    role = request.GET.get("role", "")
    q = request.GET.get("q", "").strip()
    archived = request.GET.get("show") == "archived"  # deactivated accounts live in their own tab
    everyone = User.objects.all()
    users = (everyone.filter(is_active=not archived).select_related("department")
             .select_related("shop").prefetch_related("groups", Prefetch("headed_departments", queryset=Department.objects.order_by("name")))
             .order_by("full_name"))
    if role in roles.ALL_ROLES:
        users = users.filter(groups__name=role)
    if q:
        users = users.filter(Q(username__icontains=q) | Q(full_name__icontains=q) | Q(email__icontains=q)
                             | Q(title__icontains=q))
    return render(request, "requests_app/manage/users.html", {
        "users": users[:200], "role": role, "q": q, "all_roles": roles.ALL_ROLES, "archived": archived,
        "count_students": everyone.filter(groups__name=roles.STUDENT, is_active=True).count(),
        "count_staff": everyone.filter(is_active=True).exclude(groups__name=roles.STUDENT).count(),
        "count_off": everyone.filter(is_active=False).count(),
    })


@roles.role_required(roles.ADMIN)
def manage_staff_new(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Staff account created")
        return redirect("manage_users")
    return render(request, "requests_app/manage/staff_form.html", {"form": form})


@roles.role_required(roles.ADMIN)
def manage_user_edit(request, pk):
    account = get_object_or_404(User, pk=pk)
    form = AccountEditForm(request.POST or None, instance=account, editor=request.user)
    if request.method == "POST" and form.is_valid():
        headed_before = set(account.headed_departments.values_list("name", flat=True))
        with transaction.atomic():
            form.save()
        messages.success(request, "Account updated")
        if form.new_role != form.old_role:
            messages.info(request, f"{account.display_name} is now {form.new_role}.")
        freed = sorted(headed_before - set(account.headed_departments.values_list("name", flat=True)))
        if freed:
            messages.warning(request, f"{', '.join(freed)} now {'has' if len(freed) == 1 else 'have'} "
                                      "no Program Head. Assign one on the Departments page.")
        return redirect("manage_users")
    return render(request, "requests_app/manage/user_form.html", {
        "form": form, "account": account, "account_role": roles.primary_role(account),
        "headed": account.headed_departments.order_by("name"),
    })


@require_POST
@roles.role_required(roles.ADMIN)
def manage_deactivate(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You can't deactivate your own account.")
    elif not user.is_active:
        messages.info(request, "This account is already deactivated.")
    else:
        with transaction.atomic():
            user.is_active = False
            user.save(update_fields=["is_active"])
            # A deactivated Program Head can't review, so free their departments for someone else.
            headed = list(user.headed_departments.values_list("name", flat=True))
            user.headed_departments.update(program_head=None)
        messages.success(request, "Account deactivated")
        messages.info(request, f"{user.display_name} moved to Archived.")
        if headed:
            messages.warning(request, f"{', '.join(headed)} now {'has' if len(headed) == 1 else 'have'} "
                                      "no Program Head. Assign one on the Departments page.")
    return redirect("manage_users")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_reactivate(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.is_active:
        messages.info(request, "This account is already active.")
    else:
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, "Account reactivated")
    return redirect(reverse("manage_users") + "?show=archived")


@roles.role_required(roles.ADMIN)
def manage_departments(request):
    archived = request.GET.get("show") == "archived"
    departments = Department.objects.filter(is_active=not archived).select_related("program_head").annotate(
        students=Count("members", filter=Q(members__is_active=True), distinct=True),
        open_requests=Count("members__replacement_requests",
                            filter=~Q(members__replacement_requests__status__in=CLOSED), distinct=True),
    )
    choices = AssignHeadForm.shared_choices()
    rows = [(d, AssignHeadForm(prefix=f"d{d.pk}", initial={"program_head": d.program_head_id}, choices=choices))
            for d in departments]
    return render(request, "requests_app/manage/departments.html", {
        "rows": rows, "dept_form": DepartmentForm(), "archived": archived,
        "unassigned": 0 if archived else sum(1 for d, _ in rows if not d.program_head_id),
        "count_active": Department.objects.filter(is_active=True).count(),
        "count_off": Department.objects.filter(is_active=False).count(),
    })


def _set_active(request, obj, active, noun):
    """Archive (deactivate) or reactivate a department or branch. Nothing is deleted."""
    if obj.is_active == active:
        messages.info(request, f"This {noun} is already {'active' if active else 'archived'}.")
        return False
    obj.is_active = active
    obj.save(update_fields=["is_active"])
    return True


@require_POST
@roles.role_required(roles.ADMIN)
def manage_department_archive(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if _set_active(request, dept, False, "department"):
        messages.success(request, "Department archived")
        messages.info(request, f"{dept.name} moved to Archived.")
    return redirect("manage_departments")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_department_reactivate(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if _set_active(request, dept, True, "department"):
        messages.success(request, "Department reactivated")
    return redirect(reverse("manage_departments") + "?show=archived")


def _form_errors(form):
    return " ".join(e for errs in form.errors.values() for e in errs)


@require_POST
@roles.role_required(roles.ADMIN)
def manage_department_add(request):
    form = DepartmentForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Department added")
    else:
        messages.error(request, _form_errors(form))
    return redirect("manage_departments")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_department_rename(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    form = DepartmentForm(request.POST, instance=dept)
    if form.is_valid():
        form.save()
        messages.success(request, "Department renamed")
    else:
        messages.error(request, _form_errors(form))
    return redirect("manage_departments")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_assign_head(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    form = AssignHeadForm(request.POST, prefix=f"d{dept.pk}")
    if not form.is_valid():
        messages.error(request, "Choose an active Program Head.")
        return redirect("manage_departments")
    dept.program_head = form.cleaned_data["program_head"]
    dept.save(update_fields=["program_head"])
    messages.success(request, "Program Head assigned" if dept.program_head else "Program Head removed")
    return redirect("manage_departments")


SHOP_WORK = (Status.PAID, Status.PHOTO_TAKEN, Status.PRINTED)


@roles.role_required(roles.ADMIN)
def manage_shops(request):
    archived = request.GET.get("show") == "archived"
    shops = IdShop.objects.filter(is_active=not archived).annotate(
        staff_count=Count("staff", filter=Q(staff__is_active=True, staff__groups__name=roles.ID_SHOP), distinct=True),
        in_progress=Count("requests", filter=Q(requests__status__in=SHOP_WORK), distinct=True),
        released=Count("requests", filter=Q(requests__status=Status.RELEASED), distinct=True),
    ).prefetch_related(Prefetch("staff", queryset=User.objects.filter(is_active=True, groups__name=roles.ID_SHOP)
                                .order_by("full_name"), to_attr="active_staff"))
    unassigned = (User.objects.filter(is_active=True, groups__name=roles.ID_SHOP, shop__isnull=True)
                  .order_by("full_name"))
    return render(request, "requests_app/manage/shops.html", {
        "shops": shops, "unassigned": [] if archived else unassigned, "archived": archived,
        "count_active": IdShop.objects.filter(is_active=True).count(),
        "count_off": IdShop.objects.filter(is_active=False).count(),
        "to_pay": ReplacementRequest.objects.filter(status=Status.OSAS_APPROVED).count(),
    })


@require_POST
@roles.role_required(roles.ADMIN)
def manage_shop_add(request):
    form = IdShopForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "ID shop added")
    else:
        messages.error(request, _form_errors(form))
    return redirect("manage_shops")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_shop_rename(request, pk):
    form = IdShopForm(request.POST, instance=get_object_or_404(IdShop, pk=pk))
    if form.is_valid():
        form.save()
        messages.success(request, "ID shop renamed")
    else:
        messages.error(request, _form_errors(form))
    return redirect("manage_shops")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_shop_archive(request, pk):
    shop = get_object_or_404(IdShop, pk=pk)
    if _set_active(request, shop, False, "branch"):
        messages.success(request, "ID shop archived")
        messages.info(request, f"{shop.name} moved to Archived.")
    return redirect("manage_shops")


@require_POST
@roles.role_required(roles.ADMIN)
def manage_shop_reactivate(request, pk):
    shop = get_object_or_404(IdShop, pk=pk)
    if _set_active(request, shop, True, "branch"):
        messages.success(request, "ID shop reactivated")
    return redirect(reverse("manage_shops") + "?show=archived")
