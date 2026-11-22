import mimetypes

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetDoneView, PasswordResetView
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override

from . import roles, services
from .forms import ChangePasswordForm, ForgotPasswordForm, NewPasswordForm, SignupForm
from .models import ApprovalLog, ReplacementRequest, Upload

HOME_FOR_ROLE = {
    roles.ADMIN: "manage_users",
    roles.OSAS: "osas_queue",
    roles.PROGRAM_HEAD: "ph_queue",
    roles.ID_SHOP: "shop_list",
    roles.STUDENT: "student_list",
}


@roles.role_required()
def home(request):
    role = roles.primary_role(request.user)
    if role:
        return redirect(HOME_FOR_ROLE[role])
    return render(request, "requests_app/home.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            user.groups.add(Group.objects.get(name=roles.STUDENT))
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Account created")
        return redirect("student_list")
    return render(request, "registration/signup.html", {"form": form})


@roles.role_required()
@xframe_options_sameorigin
@csp_override({})  # a page policy on the file itself can stop the browser's PDF viewer
def upload_file(request, pk):
    """Stream a private upload to the owner or the staff handling the request."""
    upload = get_object_or_404(Upload.objects.select_related("request__student__department"), pk=pk)
    if not services.can_view(request.user, upload.request):
        raise PermissionDenied
    try:
        with upload.file.open("rb") as f:
            content = f.read()
    except FileNotFoundError:
        raise Http404("File not found")
    content_type = mimetypes.guess_type(upload.file.name)[0] or "application/octet-stream"
    response = HttpResponse(content, content_type=content_type)
    filename = f"{upload.request.reference_no}-{upload.kind}.{upload.extension}"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def safe_next(request, fallback):
    """Redirect target from a ?next / POST next value, only if it points at this site."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return fallback


def show_transition_error(request, error):
    messages.error(request, str(error))


@roles.role_required(*roles.ALL_ROLES)
def authorization(request, pk):
    """The printable ID replacement authorization OSAS gives the student to show at the ID shop."""
    req = get_object_or_404(ReplacementRequest.objects.select_related("student__department"), pk=pk)
    if not services.can_view(request.user, req):
        raise PermissionDenied
    if not req.has_authorization:
        messages.info(request, "The authorization is ready once OSAS approves the request.")
        if req.student_id == request.user.pk:
            return redirect("student_detail", pk=req.pk)
        return redirect("home")
    req.with_logs()
    return render(request, "requests_app/authorization.html", {
        "req": req,
        "ph_log": req.last_log(ApprovalLog.Action.PH_APPROVED),
        "osas_log": req.last_log(ApprovalLog.Action.OSAS_APPROVED),
        "paid_log": req.last_log(ApprovalLog.Action.PAID),
        "is_owner": req.student_id == request.user.pk,
        "back_url": _authorization_back_url(request.user, req),
    })


def _authorization_back_url(user, req):
    if req.student_id == user.pk:
        return reverse("student_detail", args=[req.pk])
    if roles.has_role(user, roles.ID_SHOP):
        return reverse("shop_detail", args=[req.pk])
    if roles.has_role(user, roles.OSAS):
        return reverse("osas_detail", args=[req.pk])
    return reverse("ph_detail", args=[req.pk])


# Passwords ------------------------------------------------------------------

@roles.role_required()
def password_change(request):
    """Any logged-in user changes their own password. This device stays logged in; others are logged out."""
    form = ChangePasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed")
        return redirect("home")
    return render(request, "registration/password_change.html", {"form": form})


class ForgotPasswordView(PasswordResetView):
    """Forgot password: emails a one-time link (valid for PASSWORD_RESET_TIMEOUT) through Brevo."""
    form_class = ForgotPasswordForm
    template_name = "registration/password_reset.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_sent")


class ForgotPasswordSentView(PasswordResetDoneView):
    template_name = "registration/password_reset_sent.html"


class NewPasswordView(PasswordResetConfirmView):
    """The page a reset link opens. After saving, the person logs in with the new password."""
    form_class = NewPasswordForm
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("login")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Password changed. Log in with your new password.")
        return response
