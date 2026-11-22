import hashlib
import os
import re
from urllib.parse import urlparse
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm, UserCreationForm,
)
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.validators import RegexValidator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from . import roles
from .models import LRN_RE, Department, IdShop, Payment, Upload, User

UPLOAD_ERROR = "Couldn't upload the file. Use PDF, JPG or PNG under 5 MB."
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOADS = {
    ".pdf": [b"%PDF-"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
}


def validate_upload(f):
    """Accept only real PDF, JPG or PNG files up to 5 MB (checks the extension and the file's first bytes)."""
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in ALLOWED_UPLOADS or f.size > MAX_UPLOAD_BYTES or f.size == 0:
        raise forms.ValidationError(UPLOAD_ERROR)
    f.seek(0)
    head = f.read(16)
    f.seek(0)
    if not any(head.startswith(sig) for sig in ALLOWED_UPLOADS[ext]):
        raise forms.ValidationError(UPLOAD_ERROR)


def lrn_field():
    return forms.CharField(
        label="LRN", min_length=12, max_length=12,
        validators=[RegexValidator(LRN_RE, "Enter your 12-digit LRN.")],
        help_text="Learner Reference Number: the 12 digits on your old school ID or school records.",
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": r"\d{12}", "maxlength": "12",
                                      "autocomplete": "off"}),
    )


def clean_unique_lrn(value, exclude_pk=None):
    value = (value or "").strip()
    taken = User.objects.filter(lrn=value)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise forms.ValidationError("An account with this LRN already exists.")
    return value


class BootstrapMixin:
    """Adds Bootstrap classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxSelectMultiple):
                continue  # rendered by hand as a list of check rows
            if isinstance(w, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(w, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            w.attrs["class"] = f"{w.attrs.get('class', '')} {css}".strip()

    @property
    def has_upload_error(self):
        return any(UPLOAD_ERROR in e for errors in self.errors.values() for e in errors)


LOGIN_MAX_FAILURES = 5        # wrong passwords allowed per account...
LOGIN_LOCK_SECONDS = 15 * 60  # ...within this window, then the account's login pauses for the rest of it


class LoginForm(AuthenticationForm):
    """Django's login form with the portal's own plain wording for errors, plus a limit on wrong passwords.

    Failures are counted per username (not per IP: behind a hosting proxy everyone can share one address),
    in the cache. A correct password resets the count."""
    error_messages = {
        "invalid_login": "Wrong student ID or password. Both are case-sensitive.",
        "inactive": "This account is deactivated.",
        "too_many": "Too many wrong passwords for this account. Wait 15 minutes, then try again.",
    }

    @staticmethod
    def _fail_key(username):
        return "login-fail:" + hashlib.sha256(username.strip().lower().encode()).hexdigest()

    def clean(self):
        username = self.cleaned_data.get("username") or ""
        key = self._fail_key(username)
        if username and cache.get(key, 0) >= LOGIN_MAX_FAILURES:
            from . import audit
            audit.record("sign_in", "Login paused", username[:150])
            raise forms.ValidationError(self.error_messages["too_many"], code="too_many")
        try:
            data = super().clean()
        except forms.ValidationError as e:
            if username and getattr(e, "code", None) == "invalid_login":
                if not cache.add(key, 1, LOGIN_LOCK_SECONDS):
                    try:
                        cache.incr(key)
                    except ValueError:  # expired between add() and incr()
                        cache.add(key, 1, LOGIN_LOCK_SECONDS)
            raise
        cache.delete(key)
        return data


class SignupForm(BootstrapMixin, UserCreationForm):
    username = forms.CharField(
        label="Student ID", max_length=30,
        validators=[RegexValidator(r"^[A-Za-z0-9-]+$", "Use letters, numbers and dashes only.")],
        help_text="This is your username when you log in.",
    )
    full_name = forms.CharField(label="Full name", max_length=150)
    lrn = lrn_field()
    email = forms.EmailField(label="Email", help_text="Status updates are sent here.")
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_active=True),
                                        empty_label="Choose your department")

    class Meta:
        model = User
        fields = ["username", "full_name", "lrn", "email", "department"]

    def clean_lrn(self):
        return clean_unique_lrn(self.cleaned_data["lrn"])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The sign-up page shows one password hint under both fields (see signup.html),
        # so no per-field hints here. The password rules still apply.
        for name in ("username", "lrn", "email", "password1", "password2"):
            self.fields[name].help_text = ""
        self.fields["password2"].label = "Confirm password"

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("An account with this student ID already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class RequestForm(BootstrapMixin, forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), max_length=2000,
        help_text="What happened to your ID?",
    )
    date_lost = forms.DateField(label="Date lost", widget=forms.DateInput(attrs={"type": "date"}))
    place_lost = forms.CharField(label="Place lost", max_length=200)
    letter = forms.FileField(
        label="Letter", validators=[validate_upload],
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        help_text="PDF, JPG or PNG, up to 5 MB.",
    )
    valid_id = forms.FileField(
        label="Valid ID", validators=[validate_upload],
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        help_text="PDF, JPG or PNG, up to 5 MB.",
    )
    lrn = lrn_field()
    consent = forms.BooleanField(
        label="I agree that my letter, valid ID and LRN are stored and shown only to the school staff "
              "who handle this request.",
    )

    def __init__(self, *args, files_required=True, ask_consent=False, ask_lrn=False, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from .services import open_shops  # services imports models only; avoid a cycle at import time
        shops = list(open_shops())
        if shops:  # with no branches set up (or none staffed), every ID shop staffer handles every request
            self.fields["shop"] = forms.ModelChoiceField(
                queryset=IdShop.objects.filter(pk__in=[b.pk for b in shops]).order_by("name"),
                widget=forms.Select(attrs={"class": "form-select"}), label="ID shop branch",
                empty_label="Choose a branch",
                error_messages={"required": "Choose the ID shop branch where you'll pay and claim your ID."},
            )
            self.shop_choices = shops
        if not ask_lrn:
            del self.fields["lrn"]   # only asked of students who signed up before LRN was required
        # The form page explains the file rules once, above both upload boxes.
        self.fields["letter"].help_text = "Your letter explaining what happened."
        self.fields["valid_id"].help_text = "Any valid ID with your photo."
        if not files_required:
            for name in ("letter", "valid_id"):
                self.fields[name].required = False
        if not ask_consent:
            del self.fields["consent"]

    def clean_lrn(self):
        return clean_unique_lrn(self.cleaned_data["lrn"], exclude_pk=self.user.pk if self.user else None)

    def clean_date_lost(self):
        value = self.cleaned_data["date_lost"]
        if value > timezone.localdate():
            raise forms.ValidationError("The date lost can't be in the future.")
        return value

    def upload_files(self):
        return {
            Upload.Kind.LETTER: self.cleaned_data.get("letter"),
            Upload.Kind.VALID_ID: self.cleaned_data.get("valid_id"),
        }


class PaymentForm(BootstrapMixin, forms.Form):
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    receipt_no = forms.CharField(label="Receipt number", max_length=50, required=False)
    # The automatic number the form was filled with. Left unchanged, it means "number it automatically",
    # so if someone else took it in the meantime the next free number is used instead of an error.
    suggested_receipt = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        value = (data.get("receipt_no") or "").strip()
        if not value or value == (data.get("suggested_receipt") or "").strip():
            data["receipt_no"] = ""  # automatic
        elif Payment.objects.filter(receipt_no__iexact=value).exists():
            self.add_error("receipt_no", "This receipt number is already recorded.")
        else:
            data["receipt_no"] = value
        return data


def clean_unique_email(value, exclude_pk=None):
    email = (value or "").strip().lower()
    taken = User.objects.filter(email__iexact=email)
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise forms.ValidationError("An account with this email already exists.")
    return email


def setup_shop_field(field, current=None):
    """Active branches, plus the one the account already has (so an archived branch isn't silently dropped)."""
    field.queryset = IdShop.objects.filter(Q(is_active=True) | Q(pk=current)).order_by("name")
    field.label = "ID shop branch"
    if field.queryset.exists():
        field.empty_label = "Choose a branch"
        field.help_text = "The branch they work at. They only see requests students sent to this branch."
    else:  # an empty dropdown looks broken: say why, and where to add one
        field.empty_label = "No branches yet"
        field.help_text = format_html(
            'No branches yet. <a href="{}">Add one on the ID shops page</a>, then come back to choose it. '
            'You can also save now and choose later.', reverse("manage_shops"))


def clean_shop(form, data):
    """ID shop staff need a branch once at least one branch is set up."""
    if data.get("role") == roles.ID_SHOP and not data.get("shop") and IdShop.objects.filter(is_active=True).exists():
        form.add_error("shop", "Choose the ID shop branch they work at.")


class StaffCreateForm(BootstrapMixin, UserCreationForm):
    full_name = forms.CharField(label="Full name", max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[(r, r) for r in roles.STAFF_ROLES])
    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True), required=False, empty_label="Choose a department",
        help_text="The department they review. This replaces its current Program Head.",
    )

    class Meta:
        model = User
        fields = ["username", "honorific", "full_name", "title", "email", "shop"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "They log in with this. Letters, numbers and . _ - only."
        self.fields["honorific"].choices = [("", "None")] + list(User.Honorific.choices)
        self.fields["honorific"].help_text = ""
        self.fields["title"].help_text = "Optional, e.g. Dean, College of Computer Studies."
        self.fields["title"].widget.attrs["placeholder"] = "e.g. Department Chair"
        self.fields["password1"].help_text = "At least 8 characters. Not only numbers, and not a common password."
        self.fields["password2"].label = "Confirm password"
        self.fields["password2"].help_text = ""
        setup_shop_field(self.fields["shop"])

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("An account with this username already exists.")
        return username

    def clean_email(self):
        return clean_unique_email(self.cleaned_data["email"])

    def clean(self):
        data = super().clean()
        if data.get("role") == roles.PROGRAM_HEAD and not data.get("department"):
            self.add_error("department", "Choose the department this Program Head reviews.")
        clean_shop(self, data)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data["role"]
        if role != roles.ID_SHOP:
            user.shop = None
        if role == roles.ADMIN:
            user.is_staff = True
            user.is_superuser = True
        user.save()
        user.groups.add(Group.objects.get(name=role))
        dept = self.cleaned_data.get("department")
        if role == roles.PROGRAM_HEAD and dept:
            dept.program_head = user
            dept.save(update_fields=["program_head"])
        return user


class AccountEditForm(BootstrapMixin, forms.ModelForm):
    """The Admin fixes a name, email, honorific or position, and for staff the role and headed departments.
    Students keep their role and have no honorific or position."""

    class Meta:
        model = User
        fields = ["honorific", "full_name", "title", "email", "shop"]
        labels = {"full_name": "Full name"}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        account = self.instance
        self.old_role = roles.primary_role(account)
        self.fields["full_name"].required = True
        self.fields["email"].required = True
        if roles.has_role(account, roles.STUDENT):
            del self.fields["honorific"]
            del self.fields["title"]
            del self.fields["shop"]
            return
        setup_shop_field(self.fields["shop"], current=account.shop_id)
        self.fields["honorific"].choices = [("", "None")] + list(User.Honorific.choices)
        self.fields["honorific"].help_text = ""
        self.fields["title"].help_text = "Optional, e.g. Dean, College of Computer Studies."

        self.fields["role"] = forms.ChoiceField(
            choices=[(r, r) for r in roles.STAFF_ROLES], initial=self.old_role, widget=forms.Select(attrs={"class": "form-select"}),
            help_text="Changing it takes effect at their next page load.",
        )
        if editor is not None and editor.pk == account.pk:
            self.fields["role"].disabled = True  # an Admin can't lock themselves out
            self.fields["role"].help_text = "You can't change your own role."

        field = forms.ModelMultipleChoiceField(
            queryset=Department.objects.filter(Q(is_active=True) | Q(program_head=account))
            .select_related("program_head").order_by("name"),
            required=False, widget=forms.CheckboxSelectMultiple, label="Heads these departments",
            initial=list(account.headed_departments.values_list("pk", flat=True)),
            help_text="Ticking a department that already has a Program Head moves it to this person.",
        )
        field.label_from_instance = lambda d: (
            f"{d.name} (now {d.program_head.display_name})"
            if d.program_head_id and d.program_head_id != account.pk else d.name
        )
        self.fields["departments"] = field

    def clean_email(self):
        return clean_unique_email(self.cleaned_data["email"], exclude_pk=self.instance.pk)

    def clean(self):
        data = super().clean()
        if data.get("role") == roles.PROGRAM_HEAD and data.get("departments") and not self.instance.is_active:
            self.add_error("departments", "Reactivate this account before giving it a department.")
        if "role" in self.fields:
            clean_shop(self, data)
        return data

    @property
    def new_role(self):
        return self.cleaned_data.get("role", self.old_role)

    def save(self, commit=True):
        if "role" in self.fields and self.new_role != roles.ID_SHOP:
            self.instance.shop = None  # only ID shop staff belong to a branch
        user = super().save(commit=commit)
        if not commit or "role" not in self.fields:
            return user
        new_role = self.new_role
        if new_role != self.old_role:
            user.groups.remove(*Group.objects.filter(name__in=roles.STAFF_ROLES))
            user.groups.add(Group.objects.get(name=new_role))
            user.is_staff = user.is_superuser = new_role == roles.ADMIN
            user.save(update_fields=["is_staff", "is_superuser"])
        chosen = list(self.cleaned_data.get("departments") or []) if new_role == roles.PROGRAM_HEAD else []
        Department.objects.filter(program_head=user).exclude(pk__in=[d.pk for d in chosen]).update(program_head=None)
        Department.objects.filter(pk__in=[d.pk for d in chosen]).update(program_head=user)
        return user

class DepartmentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        taken = Department.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError("A department with this name already exists.")
        return name


class AssignHeadForm(BootstrapMixin, forms.Form):
    program_head = forms.ModelChoiceField(queryset=User.objects.none(), empty_label="No Program Head", required=False)

    def __init__(self, *args, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["program_head"]
        field.queryset = User.objects.filter(groups__name=roles.PROGRAM_HEAD, is_active=True).order_by("full_name")
        if choices is not None:  # the Departments page shares one list between all its rows (one query, not one per row)
            field.choices = choices

    @classmethod
    def shared_choices(cls):
        field = cls().fields["program_head"]
        return [("", field.empty_label)] + [(u.pk, field.label_from_instance(u)) for u in field.queryset]


class IdShopForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = IdShop
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if IdShop.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("An ID shop with this name already exists.")
        return name


# google.com, google.com.ph, google.co.uk, google.ph ... (with www. or maps.), nothing else
GOOGLE_MAPS_HOST = re.compile(r"^(?:www\.|maps\.)?google\.(?:com|com\.[a-z]{2}|co\.[a-z]{2}|[a-z]{2})$")
COORD_PATTERNS = [  # most exact first: the dropped pin, then the map's centre, then a query
    re.compile(r"!3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)"),
    re.compile(r"@(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)"),
    re.compile(r"[?&](?:q|query|ll|destination)=(-?\d{1,2}(?:\.\d+)?)(?:,|%2C)\s*(-?\d{1,3}(?:\.\d+)?)", re.I),
]


def is_google_maps_link(url):
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        return False
    if host == "maps.app.goo.gl" or (host == "goo.gl" and parts.path.startswith("/maps")):
        return True  # short links from Google Maps' Share button
    return bool(GOOGLE_MAPS_HOST.match(host)) and (host.startswith("maps.") or parts.path.startswith("/maps"))


def coords_from_maps_link(url):
    """(lat, lng) as Decimals from a full Google Maps link, or None (short share links carry none)."""
    for pattern in COORD_PATTERNS:
        m = pattern.search(url)
        if m:
            lat, lng = Decimal(m.group(1)), Decimal(m.group(2))
            if abs(lat) <= 90 and abs(lng) <= 180:
                return lat.quantize(Decimal("0.000001")), lng.quantize(Decimal("0.000001"))
    return None


class BranchLocationForm(BootstrapMixin, forms.ModelForm):
    """ID shop staff set where their branch is: they drop a pin in Google Maps and paste its link here."""

    class Meta:
        model = IdShop
        fields = ["maps_link", "address", "hours"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        link = self.fields["maps_link"]
        link.widget.attrs.update({"placeholder": "https://maps.app.goo.gl/… or https://www.google.com/maps/…",
                                  "inputmode": "url", "autocomplete": "off"})
        link.help_text = ""  # the page explains how to get the link
        self.fields["address"].help_text = "Optional."
        self.fields["address"].widget.attrs["placeholder"] = "e.g. Ground floor, Main building, Lucena City"
        self.fields["hours"].widget.attrs["placeholder"] = "e.g. Mon to Fri, 8:00 AM to 5:00 PM"

    def clean_maps_link(self):
        url = (self.cleaned_data.get("maps_link") or "").strip()
        if url and not is_google_maps_link(url):
            raise forms.ValidationError("Paste a Google Maps link (google.com/maps or maps.app.goo.gl).")
        return url

    def clean(self):
        data = super().clean()
        if not self.errors and not data.get("maps_link") and not (data.get("address") or "").strip():
            raise forms.ValidationError("Paste the Google Maps link of the branch's pin so students can find it.")
        return data

    def save(self, commit=True):
        shop = super().save(commit=False)
        coords = coords_from_maps_link(shop.maps_link) if shop.maps_link else None
        shop.latitude, shop.longitude = coords or (None, None)
        if commit:
            shop.save()
        return shop


# ---------------------------------------------------------------- passwords

PASSWORD_HELP = "At least 8 characters. Not only numbers, and not a common password."
RESET_EMAILS_PER_HOUR = 3   # reset emails one account can be sent in an hour (stops inbox flooding)


class _NewPasswordFields:
    """Plain labels for the two new-password fields, and a fresh start for the wrong-password count."""

    def _label_new_password(self):
        self.fields["new_password1"].label = "New password"
        self.fields["new_password1"].help_text = PASSWORD_HELP
        self.fields["new_password2"].label = "Confirm new password"
        self.fields["new_password2"].help_text = ""

    def save(self, commit=True):
        user = super().save(commit)
        cache.delete(LoginForm._fail_key(user.username))   # a new password ends any login pause
        return user


class ChangePasswordForm(_NewPasswordFields, BootstrapMixin, PasswordChangeForm):
    """A logged-in user changes their own password (needs the current one)."""
    error_messages = {**PasswordChangeForm.error_messages,
                      "password_incorrect": "That isn't your current password."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Current password"
        self._label_new_password()


class NewPasswordForm(_NewPasswordFields, BootstrapMixin, SetPasswordForm):
    """The page a reset link opens: set a new password."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._label_new_password()


class ForgotPasswordForm(BootstrapMixin, PasswordResetForm):
    """Forgot password: the student ID (username) or the email address, sent to the account's own email.

    The page says the same thing whether or not an account matched, so it doesn't reveal who has one."""
    email = forms.CharField(label="Student ID or email", max_length=254,
                            widget=forms.TextInput(attrs={"autocomplete": "username"}))

    def get_users(self, value):
        value = value.strip()
        if not value:
            return
        matches = User.objects.filter(Q(username__iexact=value) | Q(email__iexact=value), is_active=True)
        for user in matches:
            if not (user.email and user.has_usable_password()):
                continue
            key = f"pw-reset:{user.pk}"
            cache.add(key, 0, 60 * 60)
            if cache.get(key, 0) >= RESET_EMAILS_PER_HOUR:
                continue
            try:
                cache.incr(key)
            except ValueError:
                cache.add(key, 1, 60 * 60)
            from . import audit
            audit.record("sign_in", "Reset link emailed", user.username, actor=user)
            yield user
