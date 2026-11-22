import os
from datetime import timedelta
import uuid
from urllib.parse import quote_plus
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import prefetch_related_objects
from django.utils import timezone
from django.utils.crypto import salted_hmac

REFERENCE_RE = r"^REQ-\d{4}-\d{4,}$"
LRN_RE = r"^\d{12}$"


class User(AbstractUser):
    class Honorific(models.TextChoices):
        MR = "Mr.", "Mr."
        MS = "Ms.", "Ms."
        MRS = "Mrs.", "Mrs."
        DR = "Dr.", "Dr."
        PROF = "Prof.", "Prof."
        ENGR = "Engr.", "Engr."
        ATTY = "Atty.", "Atty."

    honorific = models.CharField(max_length=10, choices=Honorific.choices, blank=True,
                                 help_text="Staff only, e.g. Mrs. Shown before the name.")
    full_name = models.CharField(max_length=150)
    title = models.CharField("Position", max_length=100, blank=True,
                             help_text="Staff only, e.g. Dean, College of Computer Studies.")
    email = models.EmailField()
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="members",
        help_text="A student's department. Leave empty for staff.",
    )
    # Learner Reference Number (DepEd, 12 digits). Printed on the school ID; students only.
    lrn = models.CharField(
        "LRN", max_length=12, unique=True, null=True, blank=True,
        validators=[RegexValidator(LRN_RE, "Enter your 12-digit LRN.")],
        help_text="Learner Reference Number, 12 digits. Students only.",
    )
    # The ID shop branch an ID shop staffer works at. Empty for everyone else.
    shop = models.ForeignKey(
        "IdShop", null=True, blank=True, on_delete=models.SET_NULL, related_name="staff",
        help_text="ID shop staff only: the branch they work at.",
    )

    @property
    def display_name(self):
        """The name as people see it: "Mrs. Lee Ann Tabanao" (honorific only when set)."""
        name = self.full_name or self.username
        return f"{self.honorific} {name}" if self.honorific else name

    def __str__(self):
        return self.display_name


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    program_head = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="headed_departments",
    )
    # Archived departments keep their students and requests, but new students can't choose them.
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class IdShop(models.Model):
    """An ID shop branch. The student picks one when filing a request (like choosing a passport site),
    and only that branch handles it. The Admin adds and names branches; the branch's own ID shop staff
    set its address, hours and location: a Google Maps link to the pin they dropped in Google Maps."""
    name = models.CharField(max_length=150, unique=True)
    # Archived branches finish the requests they already have, but students can't choose them anymore.
    is_active = models.BooleanField("Active", default=True)
    address = models.CharField(max_length=255, blank=True, help_text="Building, street and city, as students should look for it.")
    hours = models.CharField("Open hours", max_length=150, blank=True, help_text="e.g. Mon to Fri, 8:00 AM to 5:00 PM")
    maps_link = models.URLField("Google Maps link", max_length=500, blank=True,
                                help_text="The pin dropped in Google Maps, from its Share button or the address bar.")
    # Read from the Google Maps link when it carries them (full links do; short share links don't).
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(Decimal("-90")), MaxValueValidator(Decimal("90"))])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(Decimal("-180")), MaxValueValidator(Decimal("180"))])
    location_updated_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Visit calendar (FR-16): students book a photo visit in these slots. No weekly hours = walk-in only.
    slot_minutes = models.PositiveSmallIntegerField("Minutes per slot", default=15,
                                                    validators=[MinValueValidator(5), MaxValueValidator(240)])
    slot_capacity = models.PositiveSmallIntegerField("Students per slot", default=3,
                                                     validators=[MinValueValidator(1), MaxValueValidator(50)])
    book_days_ahead = models.PositiveSmallIntegerField("Days students can book ahead", default=7,
                                                       validators=[MinValueValidator(1), MaxValueValidator(60)])

    class Meta:
        ordering = ["name"]
        verbose_name = "ID shop"

    def __str__(self):
        return self.name

    @property
    def has_pin(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def has_location(self):
        return bool(self.maps_link) or self.has_pin

    @property
    def maps_url(self):
        """Where "Open in Google Maps" goes: the saved link, else the coordinates, else a search for the
        name and address. No API key is needed for any of these."""
        if self.maps_link:
            return self.maps_link
        if self.has_pin:
            query = f"{self.latitude},{self.longitude}"
        else:
            query = ", ".join(x for x in (self.name, self.address) if x)
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)

    def card_data(self):
        """What the student's branch picker shows for this branch."""
        return {"id": self.pk, "name": self.name, "address": self.address, "hours": self.hours,
                "maps": self.maps_url, "has_location": self.has_location}


class Status(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    PH_APPROVED = "PH_APPROVED", "Approved by Program Head"
    OSAS_APPROVED = "OSAS_APPROVED", "Approved by OSAS"
    PAID = "PAID", "Paid"
    PHOTO_TAKEN = "PHOTO_TAKEN", "Photo taken"
    PRINTED = "PRINTED", "Printed"
    RELEASED = "RELEASED", "Released"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


# The main path a request follows, in order.
FLOW = [
    Status.SUBMITTED, Status.PH_APPROVED, Status.OSAS_APPROVED, Status.PAID,
    Status.PHOTO_TAKEN, Status.PRINTED, Status.RELEASED,
]
STEP_LABELS = {
    Status.SUBMITTED: "Submitted",
    Status.PH_APPROVED: "Program Head",
    Status.OSAS_APPROVED: "OSAS",
    Status.PAID: "Paid",
    Status.PHOTO_TAKEN: "Photo taken",
    Status.PRINTED: "Printed",
    Status.RELEASED: "Released",
}
NEXT_STEP_TEXT = {
    Status.SUBMITTED: "Your Program Head reviews it next.",
    Status.PH_APPROVED: "OSAS reviews it next.",
    Status.OSAS_APPROVED: "Show your authorization at the ID shop and pay the replacement fee.",
    Status.PAID: "The ID shop takes your new photo next.",
    Status.PHOTO_TAKEN: "Your new ID is being printed.",
    Status.PRINTED: "Your ID is ready. Claim it at the ID shop.",
    Status.RELEASED: "Your request is closed.",
    Status.REJECTED: "Fix what the remarks say, then resubmit from My requests.",
    Status.CANCELLED: "Nothing else to do. File a new request if you still need an ID.",
}


# Statuses where OSAS has approved the request, so its authorization slip exists.
AUTHORIZED_STATUSES = (
    Status.OSAS_APPROVED, Status.PAID, Status.PHOTO_TAKEN, Status.PRINTED, Status.RELEASED,
)


class ReplacementRequest(models.Model):
    reference_no = models.CharField(
        max_length=20, unique=True, editable=False,
        validators=[RegexValidator(REFERENCE_RE, "Use the format REQ-YYYY-0001.")],
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="replacement_requests",
    )
    reason = models.TextField()
    date_lost = models.DateField()
    place_lost = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    remarks = models.TextField(blank=True)
    # The ID shop branch the student chose. Only that branch sees and handles the request.
    # Empty for requests filed before branches existed (every branch can handle those).
    shop = models.ForeignKey(IdShop, null=True, blank=True, on_delete=models.PROTECT, related_name="requests")
    # The photo visit the student booked at their branch (FR-16): optional, changeable until the fee is paid.
    visit_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference_no

    @property
    def badge_class(self):
        """CSS classes for the status badge, e.g. "status status-ph-approved" (see app.css)."""
        return f"status status-{self.status.lower().replace('_', '-')}"

    @property
    def next_step_text(self):
        return NEXT_STEP_TEXT.get(self.status, "")

    @property
    def days_waiting(self):
        """Whole days since the last update, by the local calendar (0 = today)."""
        return (timezone.localdate() - timezone.localtime(self.updated_at).date()).days

    @property
    def shop_steps(self):
        """The ID shop's four steps as (state, label): done, current or todo."""
        reached = FLOW.index(self.status) if self.status in FLOW else -1
        steps = []
        for code in (Status.PAID, Status.PHOTO_TAKEN, Status.PRINTED, Status.RELEASED):
            i = FLOW.index(code)
            state = "done" if i <= reached else "current" if i == reached + 1 else "todo"
            steps.append((state, STEP_LABELS[code]))
        return steps

    @property
    def has_authorization(self):
        return self.status in AUTHORIZED_STATUSES

    @property
    def authorization_code(self):
        """Short code printed on the authorization slip. The ID shop sees the same code on its screen,
        so a slip that was edited or made up doesn't match. Derived from SECRET_KEY, not stored."""
        digest = salted_hmac("quik-reid.authorization", f"{self.pk}:{self.reference_no}").hexdigest().upper()
        return f"{digest[:4]}-{digest[4:8]}"

    def with_logs(self):
        """Load the approval log (with who did each step) in one query, for pages that use it several times:
        the tracker, the history list and the slip all read it from this cache."""
        prefetch_related_objects([self], models.Prefetch("logs", queryset=ApprovalLog.objects.select_related("actor")))
        return self

    def last_log(self, action):
        """The most recent approval-log entry with this action, or None."""
        if "logs" in getattr(self, "_prefetched_objects_cache", {}):
            return next((log for log in reversed(self.logs.all()) if log.action == action), None)
        return self.logs.filter(action=action).select_related("actor").order_by("-created_at", "-id").first()

    @property
    def is_open(self):
        return self.status not in (Status.RELEASED, Status.CANCELLED)

    # Visit booking (FR-16): only matters while the request waits for the fee (OSAS approved).
    @property
    def visit_end(self):
        if not (self.visit_at and self.shop_id):
            return None
        return self.visit_at + timedelta(minutes=self.shop.slot_minutes)

    @property
    def visit_missed(self):
        """Booked, the slot is over, and the student hasn't paid: they can pick a new time."""
        return bool(self.visit_at) and self.status == Status.OSAS_APPROVED and self.visit_end <= timezone.now()

    @property
    def visit_upcoming(self):
        return bool(self.visit_at) and self.status == Status.OSAS_APPROVED and not self.visit_missed

    def latest_uploads(self):
        """The newest upload of each kind."""
        latest = {}
        for upload in self.uploads.order_by("uploaded_at", "id"):
            latest[upload.kind] = upload
        return [latest[k] for k in (Upload.Kind.LETTER, Upload.Kind.VALID_ID) if k in latest]

    def rejected_from(self):
        """The status the request had when it was last rejected (SUBMITTED or PH_APPROVED)."""
        state, rejected_from = None, None
        for log in self.logs.all():  # oldest first (ApprovalLog's ordering)
            if log.action == ApprovalLog.Action.REJECTED:
                rejected_from = state
            state = ApprovalLog.STATUS_FOR_ACTION.get(log.action, state)
        return rejected_from or Status.SUBMITTED

    def step_dates(self):
        """When each step on the main path was last reached, from the approval log."""
        dates = {}
        for log in self.logs.all():
            status = ApprovalLog.STATUS_FOR_ACTION.get(log.action)
            if status in FLOW or status == Status.REJECTED:
                dates[status] = log.created_at
        return dates

    def tracker_steps(self):
        """Steps for the status tracker: each has a label, a state (done, current, rejected, todo)
        and, for finished steps, the date it happened."""
        stopped_at = None
        if self.status in FLOW:
            reached = FLOW.index(self.status)
        elif self.status == Status.REJECTED:
            reached = FLOW.index(self.rejected_from())
            stopped_at = reached + 1
        else:  # cancelled
            reached = 0
            stopped_at = -1
        dates = self.step_dates()
        steps = []
        for i, code in enumerate(FLOW):
            if i <= reached:
                state = "done"
            elif i == stopped_at:
                state = "rejected"
            elif stopped_at is None and i == reached + 1:
                state = "current"
            else:
                state = "todo"
            steps.append({"label": STEP_LABELS[code], "state": state,
                          "date": dates.get(code) if state == "done"
                          else dates.get(Status.REJECTED) if state == "rejected" else None})
        return steps


def upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".jpeg":
        ext = ".jpg"
    return f"id-portal/{instance.request.reference_no}/{instance.kind}-{uuid.uuid4().hex[:12]}{ext}"


class Upload(models.Model):
    class Kind(models.TextChoices):
        LETTER = "letter", "Letter"
        VALID_ID = "valid_id", "Valid ID"

    request = models.ForeignKey(ReplacementRequest, on_delete=models.CASCADE, related_name="uploads")
    # Stored on Cloudinary with authenticated (private) delivery. See requests_app/storage.py.
    file = models.FileField(upload_to=upload_to, max_length=255)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.request} · {self.get_kind_display()}"

    @property
    def extension(self):
        return os.path.splitext(self.file.name)[1].lower().lstrip(".")

    @property
    def is_pdf(self):
        return self.extension == "pdf"


class ApprovalLog(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        RESUBMITTED = "RESUBMITTED", "Resubmitted"
        PH_APPROVED = "PH_APPROVED", "Approved by Program Head"
        OSAS_APPROVED = "OSAS_APPROVED", "Approved by OSAS"
        REJECTED = "REJECTED", "Rejected"
        PAID = "PAID", "Payment recorded"
        PHOTO_TAKEN = "PHOTO_TAKEN", "Photo taken"
        PRINTED = "PRINTED", "Printed"
        RELEASED = "RELEASED", "Released"
        CANCELLED = "CANCELLED", "Cancelled"

    STATUS_FOR_ACTION = {a.value: (Status.SUBMITTED if a == "RESUBMITTED" else a.value) for a in Action}

    request = models.ForeignKey(ReplacementRequest, on_delete=models.CASCADE, related_name="logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20, choices=Action.choices)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.request} · {self.get_action_display()}"


class Payment(models.Model):
    request = models.OneToOneField(ReplacementRequest, on_delete=models.CASCADE, related_name="payment")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    receipt_no = models.CharField(max_length=50, unique=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.receipt_no} · {self.amount}"


class AuditLog(models.Model):
    """Activity log (FR-18): one row per action, who did it, when and from where. Rows are never changed.

    Request status changes are copied here from ApprovalLog; the rest is written with `audit.record`."""

    class Kind(models.TextChoices):
        REQUEST = "request", "Requests"
        FILE = "file", "Files"
        SIGN_IN = "sign_in", "Sign-in"
        ACCOUNT = "account", "Accounts"
        DEPARTMENT = "department", "Departments"
        BRANCH = "branch", "ID shop branches"

    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    action = models.CharField(max_length=60)
    summary = models.CharField(max_length=300)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    request = models.ForeignKey(ReplacementRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ip = models.GenericIPAddressField("IP address", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}: {self.summary}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Activity log entries can't be changed.")
        super().save(*args, **kwargs)


class BranchHours(models.Model):
    """When a branch takes photo visits on one weekday (0 = Monday). One time range per day."""
    shop = models.ForeignKey(IdShop, on_delete=models.CASCADE, related_name="hours_set")
    weekday = models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)])
    opens = models.TimeField()
    closes = models.TimeField()

    class Meta:
        ordering = ["weekday"]
        constraints = [models.UniqueConstraint(fields=["shop", "weekday"], name="one_range_per_weekday")]


class BranchClosure(models.Model):
    """A date the branch is closed (a holiday): no visit slots that day."""
    shop = models.ForeignKey(IdShop, on_delete=models.CASCADE, related_name="closures")
    date = models.DateField()
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["shop", "date"], name="one_closure_per_date")]
