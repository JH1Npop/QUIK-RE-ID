import re
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib import messages
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.shortcuts import render
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import roles, services
from .models import ApprovalLog, AuditLog, BranchHours, Department, IdShop, Payment, ReplacementRequest, Status, Upload, User

# Tests keep uploads in memory instead of sending them to Cloudinary.
TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PDF = b"%PDF-1.4\n%test\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PASSWORD = "pass-12345!"


def pdf(name="letter.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def png(name="id.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@override_settings(STORAGES=TEST_STORAGES, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PortalTestCase(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # wrong-password counts and cached sessions mustn't leak between tests

    @classmethod
    def setUpTestData(cls):
        cls.cs = Department.objects.create(name="Computer Studies")
        cls.ba = Department.objects.create(name="Business Administration")
        cls.student = cls.make_user("2026-00001", roles.STUDENT, dept=cls.cs)
        cls.other_student = cls.make_user("2026-00002", roles.STUDENT, dept=cls.ba)
        cls.ph = cls.make_user("ph-cs", roles.PROGRAM_HEAD)
        cls.ph_other = cls.make_user("ph-ba", roles.PROGRAM_HEAD)
        cls.osas = cls.make_user("osas", roles.OSAS)
        cls.shop = cls.make_user("shop", roles.ID_SHOP)
        cls.admin_user = cls.make_user("boss", roles.ADMIN)
        cls.cs.program_head = cls.ph
        cls.cs.save()
        cls.ba.program_head = cls.ph_other
        cls.ba.save()

    @classmethod
    def make_user(cls, username, role, dept=None):
        lrn = None
        if role == roles.STUDENT:
            lrn = str(100000000000 + User.objects.count())  # unique 12-digit test LRN
        user = User.objects.create_user(
            username=username, password=PASSWORD, full_name=f"User {username}",
            email=f"{username}@example.com", department=dept, lrn=lrn,
        )
        user.groups.add(Group.objects.get(name=role))
        return user

    def make_request(self, student=None, status=Status.SUBMITTED):
        """Test fixture: skips the one-open-request rule so a test can set up several requests."""
        student = student or self.student
        with mock.patch.object(services, "open_request", return_value=None):
            req = services.create_request(
                student, {"reason": "Lost it", "date_lost": date(2026, 1, 5), "place_lost": "Library"},
                {Upload.Kind.LETTER: pdf(), Upload.Kind.VALID_ID: png()},
            )
        if status != Status.SUBMITTED:
            ReplacementRequest.objects.filter(pk=req.pk).update(status=status)
            req.refresh_from_db()
        return req

    def login(self, user):
        self.client.force_login(user)

    def messages_of(self, response):
        return [str(m) for m in messages.get_messages(response.wsgi_request)]


class Phase1SetupTests(PortalTestCase):
    def test_login_page_renders(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Quik RE-ID")
        self.assertContains(resp, "Log in")

    def test_wrong_login_message_has_no_please(self):
        resp = self.client.post(reverse("login"), {"username": "nobody", "password": "wrong"})
        self.assertContains(resp, "Wrong student ID or password.")
        self.assertNotContains(resp, "Please")

    def test_home_requires_login(self):
        resp = self.client.get(reverse("home"))
        self.assertRedirects(resp, f"{reverse('login')}?next=/")

    def test_top_bar_shows_user_and_logout(self):
        self.login(self.student)
        resp = self.client.get(reverse("student_list"))
        self.assertContains(resp, self.student.full_name)
        self.assertContains(resp, f'action="{reverse("logout")}"')

    def test_logout_is_post(self):
        self.login(self.student)
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, reverse("login"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)

    def test_messages_render_as_bootstrap_alerts(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.middleware import SessionMiddleware

        request = RequestFactory().get("/")
        request.user = self.student
        SessionMiddleware(lambda r: HttpResponse()).process_request(request)
        request._messages = FallbackStorage(request)
        messages.error(request, "Something failed")
        html = render(request, "requests_app/home.html").content.decode()
        self.assertIn("alert-danger", html)
        self.assertIn("Something failed", html)


class RolesAndSeedTests(PortalTestCase):
    def test_five_groups_exist(self):
        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(roles.ALL_ROLES))

    def test_home_sends_each_role_to_its_screen(self):
        for user, url_name in [(self.student, "student_list"), (self.ph, "ph_queue"), (self.osas, "osas_queue"),
                               (self.shop, "shop_list"), (self.admin_user, "manage_users")]:
            self.login(user)
            self.assertRedirects(self.client.get(reverse("home")), reverse(url_name), fetch_redirect_response=False)

    def test_wrong_role_gets_403(self):
        self.login(self.student)
        for name in ["ph_queue", "osas_queue", "osas_reports", "shop_list", "manage_users"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.login(self.shop)
        self.assertEqual(self.client.get(reverse("student_list")).status_code, 403)

    def test_seed_demo(self):
        from django.core.management import call_command
        from io import StringIO
        call_command("seed_demo", "--password", "Demo-pass-123", stdout=StringIO())
        self.assertEqual(Department.objects.filter(name__startswith="College of").count(), 2)
        for role in roles.ALL_ROLES:
            self.assertTrue(User.objects.filter(groups__name=role, username__in=[
                "2026-00001", "programhead", "osas", "idshop", "portaladmin"]).exists(), role)
        self.assertTrue(self.client.login(username="programhead", password="Demo-pass-123"))


class ReferenceNumberTests(PortalTestCase):
    def test_reference_number_format(self):
        first = self.make_request()
        second = self.make_request()
        self.assertRegex(first.reference_no, r"^REQ-\d{4}-\d{4}$")
        year = first.created_at.year
        self.assertEqual(first.reference_no, f"REQ-{year}-0001")
        self.assertEqual(second.reference_no, f"REQ-{year}-0002")

    def test_numbering_continues_past_9999(self):
        ReplacementRequest.objects.create(
            reference_no="REQ-2030-9999", student=self.student, reason="x", date_lost=date(2026, 1, 1), place_lost="x")
        self.assertEqual(services.next_reference_no(2030), "REQ-2030-10000")


class StudentScreenTests(PortalTestCase):
    def test_signup_creates_student_with_id_as_username(self):
        resp = self.client.post(reverse("signup"), {
            "username": "2026-12345", "full_name": "Ana Reyes", "email": "ana@example.com", "lrn": "123456789012",
            "department": self.cs.pk, "password1": "Strong-pass-987", "password2": "Strong-pass-987",
        })
        self.assertRedirects(resp, reverse("student_list"))
        user = User.objects.get(username="2026-12345")
        self.assertTrue(roles.has_role(user, roles.STUDENT))
        self.assertEqual(user.department, self.cs)
        self.assertEqual(user.lrn, "123456789012")

    def test_signup_lrn_must_be_12_digits_and_unique(self):
        base = {"full_name": "Ana Reyes", "department": self.cs.pk,
                "password1": "Strong-pass-987", "password2": "Strong-pass-987"}
        for i, (lrn, error) in enumerate([("12345", "12-digit"), ("12345678901a", "12-digit"),
                                          (self.student.lrn, "already exists"), ("", "required")]):
            resp = self.client.post(reverse("signup"), dict(base, username=f"2026-9{i}", email=f"a{i}@example.com", lrn=lrn))
            self.assertEqual(resp.status_code, 200, lrn)
            self.assertContains(resp, error)
        self.assertFalse(User.objects.filter(username__startswith="2026-9").exists())

    def test_student_without_lrn_gives_it_on_next_request(self):
        User.objects.filter(pk=self.student.pk).update(lrn=None)
        self.login(self.student)
        self.assertContains(self.client.get(reverse("student_new")), 'name="lrn"')
        data = {"reason": "Lost", "date_lost": "2026-01-02", "place_lost": "Gym", "consent": "on",
                "letter": pdf(), "valid_id": png()}
        resp = self.client.post(reverse("student_new"), data)
        self.assertEqual(resp.status_code, 200)  # LRN missing
        data.update(lrn="555555555555", letter=pdf(), valid_id=png())
        self.client.post(reverse("student_new"), data)
        self.student.refresh_from_db()
        self.assertEqual(self.student.lrn, "555555555555")
        ReplacementRequest.objects.update(status=Status.RELEASED)  # close it so the form opens again
        self.assertNotContains(self.client.get(reverse("student_new")), 'name="lrn"')

    def test_staff_see_the_lrn(self):
        req = self.make_request()
        self.login(self.ph)
        self.assertContains(self.client.get(reverse("ph_detail", args=[req.pk])), self.student.lrn)

    def test_submit_request_with_consent_on_first_request(self):
        self.login(self.student)
        data = {"reason": "Lost", "date_lost": "2026-01-02", "place_lost": "Gym", "letter": pdf(), "valid_id": png()}
        resp = self.client.post(reverse("student_new"), data)
        self.assertEqual(resp.status_code, 200)  # consent missing
        self.assertFalse(ReplacementRequest.objects.exists())

        data.update(consent="on", letter=pdf(), valid_id=png())
        with self.captureOnCommitCallbacks(execute=True):  # emails go out after the commit
            resp = self.client.post(reverse("student_new"), data)
        req = ReplacementRequest.objects.get()
        self.assertRedirects(resp, reverse("student_detail", args=[req.pk]))
        self.assertIn(f"Request submitted. Reference no. {req.reference_no}", self.messages_of(resp))
        self.assertEqual(req.uploads.count(), 2)
        self.assertEqual(req.logs.get().action, ApprovalLog.Action.SUBMITTED)
        self.assertEqual(len(mail.outbox), 1)

        # Second request (after the first is closed): no consent box
        services.change_status(req, Status.CANCELLED, self.student)
        resp = self.client.get(reverse("student_new"))
        self.assertNotContains(resp, 'name="consent"')

    def test_one_open_request_at_a_time(self):
        req = self.make_request(status=Status.REJECTED)  # rejected still counts as open
        self.login(self.student)
        blocked = f"You already have a request in progress ({req.reference_no})."
        resp = self.client.get(reverse("student_new"))
        self.assertRedirects(resp, reverse("student_detail", args=[req.pk]))
        self.assertTrue(any(m.startswith(blocked) for m in self.messages_of(resp)))
        resp = self.client.post(reverse("student_new"), {
            "reason": "Lost", "date_lost": "2026-01-02", "place_lost": "Gym", "letter": pdf(), "valid_id": png(),
        })
        self.assertRedirects(resp, reverse("student_detail", args=[req.pk]))
        self.assertEqual(ReplacementRequest.objects.count(), 1)
        with self.assertRaises(services.TransitionError):
            services.create_request(self.student, {"reason": "x", "date_lost": date(2026, 1, 2), "place_lost": "Gym"},
                                    {Upload.Kind.LETTER: pdf(), Upload.Kind.VALID_ID: png()})
        list_page = self.client.get(reverse("student_list"))
        self.assertNotContains(list_page, reverse("student_new"))
        self.assertContains(list_page, "One request at a time.")

        # Once it's closed, a new one can be filed.
        ReplacementRequest.objects.filter(pk=req.pk).update(status=Status.RELEASED)
        self.assertEqual(self.client.get(reverse("student_new")).status_code, 200)
        self.assertContains(self.client.get(reverse("student_list")), reverse("student_new"))

    def test_bad_upload_is_refused(self):
        self.login(self.student)
        fake = SimpleUploadedFile("letter.pdf", b"not really a pdf", content_type="application/pdf")
        big = SimpleUploadedFile("id.png", PNG + b"\x00" * (5 * 1024 * 1024), content_type="image/png")
        exe = SimpleUploadedFile("letter.exe", PDF)
        for letter, valid_id in [(fake, png()), (pdf(), big), (exe, png())]:
            resp = self.client.post(reverse("student_new"), {
                "reason": "Lost", "date_lost": "2026-01-02", "place_lost": "Gym",
                "letter": letter, "valid_id": valid_id, "consent": "on",
            })
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Couldn&#x27;t upload the file. Use PDF, JPG or PNG under 5 MB.")
        self.assertFalse(ReplacementRequest.objects.exists())

    def test_student_cant_open_another_students_request(self):
        theirs = self.make_request(student=self.other_student)
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("student_detail", args=[theirs.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("student_cancel", args=[theirs.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("student_resubmit", args=[theirs.pk])).status_code, 403)
        upload = theirs.uploads.first()
        self.assertEqual(self.client.get(reverse("upload_file", args=[upload.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("student_list")), theirs.reference_no)

    def test_owner_can_view_own_upload(self):
        req = self.make_request()
        self.login(self.student)
        resp = self.client.get(reverse("upload_file", args=[req.uploads.get(kind="letter").pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertEqual(resp.content, PDF)

    def test_cancel_and_resubmit(self):
        req = self.make_request()
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("student_cancel", args=[req.pk])).status_code, 405)
        resp = self.client.post(reverse("student_cancel", args=[req.pk]))
        self.assertIn("Request cancelled", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.CANCELLED)

        rejected = self.make_request()
        services.change_status(rejected, Status.REJECTED, self.ph, remarks="Letter is unsigned")
        resp = self.client.post(reverse("student_resubmit", args=[rejected.pk]), {
            "reason": "Lost, letter now signed", "date_lost": "2026-01-02", "place_lost": "Gym", "letter": pdf("signed.pdf"),
        })
        self.assertIn("Request resubmitted", self.messages_of(resp))
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Status.SUBMITTED)
        self.assertEqual(rejected.remarks, "")
        self.assertEqual(rejected.logs.last().action, ApprovalLog.Action.RESUBMITTED)


class ReviewScreenTests(PortalTestCase):
    def test_program_head_sees_only_own_department(self):
        mine = self.make_request(student=self.student)
        theirs = self.make_request(student=self.other_student)
        self.login(self.ph)
        resp = self.client.get(reverse("ph_queue"))
        self.assertContains(resp, mine.reference_no)
        self.assertNotContains(resp, theirs.reference_no)
        self.assertEqual(self.client.get(reverse("ph_detail", args=[theirs.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("ph_approve", args=[theirs.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("upload_file", args=[theirs.uploads.first().pk])).status_code, 403)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.SUBMITTED)

    def test_program_head_approves(self):
        req = self.make_request()
        self.login(self.ph)
        resp = self.client.post(reverse("ph_approve", args=[req.pk]))
        self.assertIn("Request approved", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.PH_APPROVED)
        log = req.logs.last()
        self.assertEqual((log.action, log.actor), (ApprovalLog.Action.PH_APPROVED, self.ph))

    def test_empty_reject_reason_is_refused(self):
        req = self.make_request()
        self.login(self.ph)
        for remarks in ["", "   "]:
            resp = self.client.post(reverse("ph_reject", args=[req.pk]), {"remarks": remarks})
            self.assertIn("Enter a reason for rejecting the request.", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.SUBMITTED)
        with self.assertRaises(services.TransitionError):
            services.change_status(req, Status.REJECTED, self.ph, remarks="")

    def test_reject_with_reason_notifies_student(self):
        req = self.make_request()
        mail.outbox.clear()
        self.login(self.ph)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(reverse("ph_reject", args=[req.pk]), {"remarks": "Blurry ID"})
        self.assertIn("Request rejected. The student was notified.", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual((req.status, req.remarks), (Status.REJECTED, "Blurry ID"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Blurry ID", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, [self.student.email])

    def test_osas_queue_flags_earlier_request_and_approves(self):
        self.make_request(status=Status.CANCELLED)
        req = self.make_request(status=Status.PH_APPROVED)
        self.login(self.osas)
        resp = self.client.get(reverse("osas_queue"))
        self.assertContains(resp, req.reference_no)
        self.assertContains(resp, "Earlier request")
        resp = self.client.post(reverse("osas_approve", args=[req.pk]))
        self.assertIn(f"Authorization {req.reference_no} created", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.OSAS_APPROVED)

    def test_authorization_slip(self):
        req = self.make_request()
        url = reverse("authorization", args=[req.pk])
        # Not before OSAS approves: the student is sent back with a note.
        self.login(self.student)
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("student_detail", args=[req.pk]), fetch_redirect_response=False)
        self.assertIn("The authorization is ready once OSAS approves the request.", self.messages_of(resp))
        services.change_status(req, Status.PH_APPROVED, self.ph)
        services.change_status(req, Status.OSAS_APPROVED, self.osas)
        # The student sees it, linked from their request page.
        self.assertContains(self.client.get(reverse("student_detail", args=[req.pk])), url)
        resp = self.client.get(url)
        self.assertContains(resp, "ID replacement authorization")
        self.assertContains(resp, req.reference_no)
        self.assertContains(resp, req.authorization_code)
        self.assertContains(resp, self.osas.display_name)
        self.assertContains(resp, self.ph.display_name)
        self.assertContains(resp, "Not yet paid")
        # The ID shop sees the same code on its screen and can open the slip.
        self.login(self.shop)
        self.assertContains(self.client.get(reverse("shop_detail", args=[req.pk])), req.authorization_code)
        self.assertEqual(self.client.get(url).status_code, 200)
        # After payment the slip shows it was used.
        services.record_payment(req, self.shop, Decimal("150.00"), "OR-1")
        self.assertContains(self.client.get(url), "Fee paid")
        # Another student can't see it.
        self.login(self.other_student)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_authorization_code_is_stable_and_unique(self):
        a, b = self.make_request(), self.make_request(student=self.other_student)
        self.assertRegex(a.authorization_code, r"^[0-9A-F]{4}-[0-9A-F]{4}$")
        self.assertEqual(a.authorization_code, ReplacementRequest.objects.get(pk=a.pk).authorization_code)
        self.assertNotEqual(a.authorization_code, b.authorization_code)

    def test_osas_reports_render(self):
        self.make_request()
        self.login(self.osas)
        resp = self.client.get(reverse("osas_reports"))
        self.assertContains(resp, "Requests per month")
        self.assertContains(resp, "Computer Studies")


class StatusRuleTests(PortalTestCase):
    def test_only_allowed_status_changes(self):
        actors = {roles.STUDENT: self.student, roles.PROGRAM_HEAD: self.ph, roles.OSAS: self.osas, roles.ID_SHOP: self.shop}
        for from_status in Status.values:
            for to_status in Status.values:
                req = self.make_request(status=from_status)
                if to_status == Status.RELEASED:
                    Payment.objects.create(request=req, amount=Decimal("100"), receipt_no=f"R-{req.pk}", recorded_by=self.shop)
                allowed_role = services.ALLOWED_CHANGES.get((from_status, to_status))
                for role, actor in actors.items():
                    should_work = role == allowed_role
                    try:
                        services.change_status(req, to_status, actor, remarks="Reason")
                        worked = True
                    except (services.TransitionError, PermissionDenied):
                        worked = False
                    self.assertEqual(worked, should_work, f"{from_status} -> {to_status} by {role}")
                    if worked:
                        break

    def test_release_needs_payment(self):
        req = self.make_request(status=Status.PRINTED)
        self.login(self.shop)
        resp = self.client.post(reverse("shop_release", args=[req.pk]))
        self.assertIn("Record a payment before releasing the ID.", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.PRINTED)

    def test_id_shop_full_flow(self):
        req = self.make_request(status=Status.OSAS_APPROVED)
        self.login(self.shop)
        resp = self.client.post(reverse("shop_payment", args=[req.pk]), {"amount": "150.00", "receipt_no": "OR-1001"})
        self.assertIn("Payment recorded", self.messages_of(resp))
        resp = self.client.post(reverse("shop_photo", args=[req.pk]))
        self.assertIn("Status updated", self.messages_of(resp))
        resp = self.client.post(reverse("shop_printed", args=[req.pk]))
        self.assertIn("Status updated", self.messages_of(resp))
        resp = self.client.post(reverse("shop_release", args=[req.pk]))
        self.assertIn("ID released", self.messages_of(resp))
        req.refresh_from_db()
        self.assertEqual(req.status, Status.RELEASED)
        self.assertEqual(req.payment.receipt_no, "OR-1001")
        self.assertEqual(
            list(req.logs.values_list("action", flat=True)),
            ["SUBMITTED", "PAID", "PHOTO_TAKEN", "PRINTED", "RELEASED"],
        )

    def test_shop_search_and_hides_earlier_statuses(self):
        early = self.make_request(status=Status.PH_APPROVED)
        ready = self.make_request(status=Status.OSAS_APPROVED)
        self.login(self.shop)
        resp = self.client.get(reverse("shop_list"), {"q": ready.reference_no})
        self.assertContains(resp, ready.reference_no)
        self.assertNotContains(resp, early.reference_no)
        self.assertEqual(self.client.get(reverse("shop_detail", args=[early.pk])).status_code, 404)

    def test_receipt_numbers_are_automatic(self):
        from django.utils import timezone
        year = timezone.localdate().year
        first = self.make_request(status=Status.OSAS_APPROVED)
        second = self.make_request(student=self.other_student, status=Status.OSAS_APPROVED)
        third = self.make_request(status=Status.OSAS_APPROVED)
        self.login(self.shop)
        # The payment window is prefilled with the next number.
        self.assertContains(self.client.get(reverse("shop_detail", args=[first.pk])), f'value="OR-{year}-0001"')
        # Left as suggested: numbered automatically, and the receipt is shown.
        resp = self.client.post(reverse("shop_payment", args=[first.pk]),
                                {"amount": "150", "receipt_no": f"OR-{year}-0001", "suggested_receipt": f"OR-{year}-0001"})
        self.assertIn(f"Receipt no. OR-{year}-0001", self.messages_of(resp))
        # A stale suggestion (someone else used it first) gets the next free number instead of an error.
        resp = self.client.post(reverse("shop_payment", args=[second.pk]),
                                {"amount": "150", "receipt_no": f"OR-{year}-0001", "suggested_receipt": f"OR-{year}-0001"})
        self.assertIn(f"Receipt no. OR-{year}-0002", self.messages_of(resp))
        # A typed paper receipt number is kept as is, and a duplicate one is refused.
        resp = self.client.post(reverse("shop_payment", args=[third.pk]),
                                {"amount": "150", "receipt_no": f"OR-{year}-0002", "suggested_receipt": f"OR-{year}-0003"})
        self.assertTrue(any("already recorded" in m for m in self.messages_of(resp)))
        resp = self.client.post(reverse("shop_payment", args=[third.pk]),
                                {"amount": "150", "receipt_no": "OR 778812", "suggested_receipt": f"OR-{year}-0003"})
        self.assertIn("Receipt no. OR 778812", self.messages_of(resp))
        self.assertEqual(services.next_receipt_no(), f"OR-{year}-0003")

    def test_shop_stage_tiles_and_search_by_name(self):
        pay = self.make_request(status=Status.OSAS_APPROVED)
        printed = self.make_request(student=self.other_student, status=Status.PRINTED)
        self.login(self.shop)
        resp = self.client.get(reverse("shop_list"), {"show": "claim"})
        self.assertContains(resp, printed.reference_no)
        self.assertNotContains(resp, pay.reference_no)
        self.assertContains(resp, "Ready to claim")
        resp = self.client.get(reverse("shop_list"), {"q": self.other_student.full_name})
        self.assertContains(resp, printed.reference_no)
        self.assertNotContains(resp, pay.reference_no)
        # The request page puts the next step in the header and shows the code to check.
        resp = self.client.get(reverse("shop_detail", args=[pay.pk]))
        self.assertContains(resp, "Check the student's authorization slip, then collect the fee.")
        self.assertContains(resp, pay.authorization_code)

    def test_status_changes_need_post(self):
        req = self.make_request()
        self.login(self.ph)
        self.assertEqual(self.client.get(reverse("ph_approve", args=[req.pk])).status_code, 405)

    def test_status_changes_need_csrf(self):
        from django.test import Client
        req = self.make_request()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.ph)
        self.assertEqual(client.post(reverse("ph_approve", args=[req.pk])).status_code, 403)


class AdminScreenTests(PortalTestCase):
    def test_create_program_head_and_assign_department(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_staff_new"), {
            "username": "newph", "full_name": "New Head", "email": "nh@example.com", "role": roles.PROGRAM_HEAD,
            "department": self.ba.pk, "password1": "Strong-pass-987", "password2": "Strong-pass-987",
        })
        self.assertIn("Staff account created", self.messages_of(resp))
        self.ba.refresh_from_db()
        self.assertEqual(self.ba.program_head.username, "newph")

    def test_honorific_and_position(self):
        self.login(self.admin_user)
        self.client.post(reverse("manage_staff_new"), {
            "username": "drcruz", "honorific": "Mrs.", "full_name": "Lee Ann Tabanao", "title": "Dean, CCS",
            "email": "lat@example.com", "role": roles.OSAS, "password1": "Strong-pass-987", "password2": "Strong-pass-987",
        })
        staff = User.objects.get(username="drcruz")
        self.assertEqual(staff.display_name, "Mrs. Lee Ann Tabanao")
        self.assertEqual(str(staff), "Mrs. Lee Ann Tabanao")
        page = self.client.get(reverse("manage_users"))
        self.assertContains(page, "Mrs. Lee Ann Tabanao")
        self.assertContains(page, "Dean, CCS")
        # Shown in the top bar and in the approval history the student's reviewers see.
        self.login(staff)
        self.assertContains(self.client.get(reverse("osas_queue")), "Mrs. Lee Ann Tabanao")
        req = self.make_request(status=Status.PH_APPROVED)
        services.change_status(req, Status.OSAS_APPROVED, staff)
        self.assertContains(self.client.get(reverse("osas_detail", args=[req.pk])), "by Mrs. Lee Ann Tabanao")

    def test_staff_form_rejects_taken_username_and_email(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_staff_new"), {
            "username": self.osas.username.upper(), "full_name": "X", "email": self.osas.email.upper(),
            "role": roles.OSAS, "password1": "Strong-pass-987", "password2": "Strong-pass-987",
        })
        self.assertContains(resp, "An account with this username already exists.")
        self.assertContains(resp, "An account with this email already exists.")

    def test_edit_account(self):
        self.login(self.admin_user)
        url = reverse("manage_user_edit", args=[self.ph.pk])
        self.assertContains(self.client.get(url), 'name="honorific"')
        resp = self.client.post(url, {"honorific": "Dr.", "full_name": "Fixed Name", "title": "Chair",
                                      "email": "fixed@example.com", "role": roles.PROGRAM_HEAD,
                                      "departments": [self.cs.pk]})
        self.assertRedirects(resp, reverse("manage_users"), fetch_redirect_response=False)
        self.assertIn("Account updated", self.messages_of(resp))
        self.ph.refresh_from_db()
        self.assertEqual((self.ph.display_name, self.ph.title, self.ph.email), ("Dr. Fixed Name", "Chair", "fixed@example.com"))
        # Students have no honorific or position, and emails stay unique.
        student_url = reverse("manage_user_edit", args=[self.student.pk])
        self.assertNotContains(self.client.get(student_url), 'name="honorific"')
        resp = self.client.post(student_url, {"full_name": "S", "email": "FIXED@example.com", "honorific": "Dr."})
        self.assertContains(resp, "An account with this email already exists.")
        self.student.refresh_from_db()
        self.assertEqual(self.student.honorific, "")
        # Only the Admin may edit.
        self.login(self.osas)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_edit_program_head_departments(self):
        self.login(self.admin_user)
        url = reverse("manage_user_edit", args=[self.ph.pk])
        page = self.client.get(url)
        self.assertContains(page, "Heads these departments")
        self.assertContains(self.client.get(reverse("manage_user_edit", args=[self.osas.pk])),
                            'data-show-value="Program Head"')  # the list only shows while the role is Program Head
        base = {"full_name": self.ph.full_name, "email": self.ph.email, "role": roles.PROGRAM_HEAD}
        # Give the Program Head both departments (the second one moves from nobody / another head).
        resp = self.client.post(url, {**base, "departments": [self.cs.pk, self.ba.pk]})
        self.assertIn("Account updated", self.messages_of(resp))
        self.assertEqual(set(self.ph.headed_departments.values_list("pk", flat=True)), {self.cs.pk, self.ba.pk})
        # Untick one: it's freed.
        self.client.post(url, {**base, "departments": [self.ba.pk]})
        self.cs.refresh_from_db()
        self.assertIsNone(self.cs.program_head)
        # A deactivated Program Head can't be given a department.
        self.client.post(reverse("manage_deactivate", args=[self.ph.pk]))
        resp = self.client.post(url, {**base, "departments": [self.cs.pk]})
        self.assertContains(resp, "Reactivate this account before giving it a department.")

    def test_change_staff_role(self):
        self.login(self.admin_user)
        url = reverse("manage_user_edit", args=[self.ph.pk])
        base = {"full_name": self.ph.full_name, "email": self.ph.email}
        headed = list(self.ph.headed_departments.values_list("name", flat=True))
        self.assertTrue(headed)
        # Program Head -> OSAS: the role changes and their departments are freed, with a warning.
        resp = self.client.post(url, {**base, "role": roles.OSAS, "departments": [self.cs.pk]})
        msgs = self.messages_of(resp)
        self.assertIn("Account updated", msgs)
        self.assertTrue(any("is now OSAS" in m for m in msgs))
        self.assertTrue(any("no Program Head" in m for m in msgs))
        self.ph.refresh_from_db()
        self.assertEqual(set(self.ph.groups.values_list("name", flat=True)), {roles.OSAS})
        self.assertFalse(self.ph.headed_departments.exists())
        self.login(self.ph)
        self.assertEqual(self.client.get(reverse("osas_queue")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ph_queue")).status_code, 403)
        # Back to Program Head with a department; to Admin sets the superuser flags.
        self.login(self.admin_user)
        self.client.post(url, {**base, "role": roles.PROGRAM_HEAD, "departments": [self.cs.pk]})
        self.cs.refresh_from_db()
        self.assertEqual(self.cs.program_head, self.ph)
        self.client.post(url, {**base, "role": roles.ADMIN})
        self.ph.refresh_from_db()
        self.assertTrue(self.ph.is_superuser and self.ph.is_staff)
        self.assertFalse(Department.objects.filter(program_head=self.ph).exists())
        # Students have no role field, and an Admin can't change their own role.
        self.assertNotContains(self.client.get(reverse("manage_user_edit", args=[self.student.pk])), 'name="role"')
        own = reverse("manage_user_edit", args=[self.admin_user.pk])
        self.client.post(own, {"full_name": "A", "email": self.admin_user.email, "role": roles.OSAS})
        self.assertTrue(roles.has_role(User.objects.get(pk=self.admin_user.pk), roles.ADMIN))

    def test_deactivated_accounts_are_archived(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_deactivate", args=[self.student.pk]))
        self.assertIn(f"{self.student.display_name} moved to Archived.", self.messages_of(resp))
        def cell(u):
            return f'<td data-label="Username">{u.username}</td>'
        active = self.client.get(reverse("manage_users"))
        self.assertNotContains(active, cell(self.student))
        self.assertContains(active, cell(self.osas))
        archived = self.client.get(reverse("manage_users") + "?show=archived")
        self.assertContains(archived, cell(self.student))
        self.assertNotContains(archived, cell(self.osas))
        self.assertContains(archived, "Reactivate")
        # Search stays inside the tab.
        self.assertContains(self.client.get(reverse("manage_users") + f"?show=archived&q={self.student.username}"),
                            cell(self.student))
        self.assertNotContains(self.client.get(reverse("manage_users") + f"?q={self.student.username}"), cell(self.student))
        # The account's requests are kept.
        resp = self.client.post(reverse("manage_reactivate", args=[self.student.pk]))
        self.assertRedirects(resp, reverse("manage_users") + "?show=archived", fetch_redirect_response=False)

    def test_reactivate_account(self):
        self.login(self.admin_user)
        self.client.post(reverse("manage_deactivate", args=[self.student.pk]))
        self.assertEqual(self.client.get(reverse("manage_reactivate", args=[self.student.pk])).status_code, 405)
        resp = self.client.post(reverse("manage_reactivate", args=[self.student.pk]))
        self.assertIn("Account reactivated", self.messages_of(resp))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)
        resp = self.client.post(reverse("manage_reactivate", args=[self.student.pk]))
        self.assertIn("This account is already active.", self.messages_of(resp))

    def test_deactivating_program_head_frees_department(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_deactivate", args=[self.ph.pk]))
        msgs = self.messages_of(resp)
        self.assertIn("Account deactivated", msgs)
        self.assertTrue(any("now has no Program Head" in m for m in msgs))
        self.assertFalse(Department.objects.filter(program_head=self.ph).exists())
        self.assertContains(self.client.get(reverse("manage_departments")), "without a Program Head")

    def test_cant_deactivate_self(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_deactivate", args=[self.admin_user.pk]))
        self.assertIn("You can't deactivate your own account.", self.messages_of(resp))

    def test_departments_add_rename_and_assign(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_department_add"), {"name": "College of Nursing"})
        self.assertIn("Department added", self.messages_of(resp))
        resp = self.client.post(reverse("manage_department_add"), {"name": "college of nursing"})
        self.assertIn("A department with this name already exists.", self.messages_of(resp))
        nursing = Department.objects.get(name="College of Nursing")
        resp = self.client.post(reverse("manage_department_rename", args=[nursing.pk]), {"name": "College of Health"})
        self.assertIn("Department renamed", self.messages_of(resp))
        nursing.refresh_from_db()
        self.assertEqual(nursing.name, "College of Health")
        self.assertEqual(self.client.get(reverse("manage_department_rename", args=[nursing.pk])).status_code, 405)
        prefix = f"d{nursing.pk}"
        resp = self.client.post(reverse("manage_assign_head", args=[nursing.pk]), {f"{prefix}-program_head": self.ph.pk})
        self.assertIn("Program Head assigned", self.messages_of(resp))
        resp = self.client.post(reverse("manage_assign_head", args=[nursing.pk]), {f"{prefix}-program_head": self.osas.pk})
        self.assertIn("Choose an active Program Head.", self.messages_of(resp))
        resp = self.client.post(reverse("manage_assign_head", args=[nursing.pk]), {f"{prefix}-program_head": ""})
        self.assertIn("Program Head removed", self.messages_of(resp))
        page = self.client.get(reverse("manage_departments"))
        self.assertContains(page, "College of Health")
        self.assertContains(page, "Rename this department?")

    def test_deactivate_account(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_deactivate", args=[self.student.pk]))
        self.assertIn("Account deactivated", self.messages_of(resp))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username=self.student.username, password=PASSWORD))

    def test_django_admin_hidden_and_superuser_only(self):
        from django.conf import settings
        if not settings.ADMIN_URL:
            self.skipTest("Django admin is switched off (no ADMIN_URL)")
        if settings.ADMIN_URL != "admin/":
            self.assertEqual(self.client.get("/admin/").status_code, 404)
        self.make_request()
        # Staff flag alone isn't enough
        self.osas.is_staff = True
        self.osas.save()
        self.login(self.osas)
        self.assertNotEqual(self.client.get(reverse("admin:index")).status_code, 200)
        # A superuser sees every model
        self.admin_user.is_staff = self.admin_user.is_superuser = True
        self.admin_user.save()
        self.login(self.admin_user)
        for model in ["user", "department", "replacementrequest", "upload", "approvallog", "payment"]:
            url = reverse(f"admin:requests_app_{model}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, model)
        # The portal never links to it
        self.assertNotContains(self.client.get(reverse("manage_users")), reverse("admin:index"))


class IdShopBranchTests(PortalTestCase):
    """Several ID shop branches: the student chooses one when filing, and only that branch handles it."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main = IdShop.objects.create(name="Main campus", address="Main building, Lucena City",
                                         latitude=Decimal("13.941400"), longitude=Decimal("121.623400"))
        cls.annex = IdShop.objects.create(name="Annex")
        cls.empty = IdShop.objects.create(name="No staff yet")
        cls.shop.shop = cls.main
        cls.shop.save()
        cls.shop_b = cls.make_user("shop-b", roles.ID_SHOP)
        cls.shop_b.shop = cls.annex
        cls.shop_b.save()

    def request_at(self, branch, status=Status.OSAS_APPROVED):
        req = self.make_request(status=status)
        ReplacementRequest.objects.filter(pk=req.pk).update(shop=branch)
        req.refresh_from_db()
        return req

    def form_data(self, **extra):
        return {"reason": "Lost it", "date_lost": "2026-01-05", "place_lost": "Library", "consent": "on",
                "letter": pdf(), "valid_id": png(), **extra}

    def pay_at(self, user, req, receipt=""):
        self.login(user)
        return self.client.post(reverse("shop_payment", args=[req.pk]), {"amount": "150.00", "receipt_no": receipt})

    def test_student_chooses_a_staffed_branch_when_filing(self):
        self.login(self.student)
        resp = self.client.get(reverse("student_new"))
        self.assertContains(resp, "Main campus")
        self.assertContains(resp, "Annex")
        self.assertNotContains(resp, "No staff yet")  # nobody there to handle it
        self.assertContains(resp, "https://www.google.com/maps/search/?api=1&amp;query=13.941400%2C121.623400")
        resp = self.client.post(reverse("student_new"), self.form_data())
        self.assertContains(resp, "Choose the ID shop branch where you")
        self.client.post(reverse("student_new"), self.form_data(shop=self.empty.pk))
        self.assertFalse(ReplacementRequest.objects.exists())
        self.client.post(reverse("student_new"), self.form_data(shop=self.annex.pk))
        self.assertEqual(ReplacementRequest.objects.get().shop, self.annex)

    def test_no_branch_choice_without_staffed_branches(self):
        User.objects.filter(shop__isnull=False).update(shop=None)  # branches exist, but nobody works at one
        self.login(self.student)
        self.assertNotContains(self.client.get(reverse("student_new")), 'name="shop"')
        self.client.post(reverse("student_new"), self.form_data())
        self.assertIsNone(ReplacementRequest.objects.get().shop)

    def test_only_the_chosen_branch_sees_the_request(self):
        req = self.request_at(self.annex)
        self.login(self.shop)  # Main campus
        self.assertNotContains(self.client.get(reverse("shop_list") + "?show=all"), req.reference_no)
        stages = {s["key"]: s["count"] for s in self.client.get(reverse("shop_list")).context["stages"]}
        self.assertEqual(stages["pay"], 0)
        self.assertEqual(self.client.get(reverse("shop_detail", args=[req.pk])).status_code, 403)
        self.assertEqual(self.pay_at(self.shop, req).status_code, 403)
        self.assertEqual(self.client.get(reverse("upload_file", args=[req.uploads.first().pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("authorization", args=[req.pk])).status_code, 403)
        # Annex sees it, takes the payment and finishes it; the branch doesn't change.
        self.login(self.shop_b)
        stages = {s["key"]: s["count"] for s in self.client.get(reverse("shop_list")).context["stages"]}
        self.assertEqual(stages["pay"], 1)
        self.pay_at(self.shop_b, req)
        for name in ("shop_photo", "shop_printed", "shop_release"):
            self.client.post(reverse(name, args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual((req.status, req.shop), (Status.RELEASED, self.annex))
        with self.assertRaises(PermissionDenied):
            services.change_status(self.request_at(self.annex, Status.PAID), Status.PHOTO_TAKEN, self.shop)

    def test_requests_without_a_branch_stay_open_to_every_branch(self):
        req = self.make_request(status=Status.OSAS_APPROVED)  # filed before branches existed
        for user in (self.shop, self.shop_b):
            self.login(user)
            self.assertEqual(self.client.get(reverse("shop_detail", args=[req.pk])).status_code, 200)
        self.pay_at(self.shop, req)
        req.refresh_from_db()
        self.assertEqual(req.shop, self.main)  # the paying branch keeps it
        self.login(self.shop_b)
        self.assertEqual(self.client.get(reverse("shop_detail", args=[req.pk])).status_code, 403)

    def test_resubmit_can_change_the_branch(self):
        req = self.request_at(self.main, Status.REJECTED)
        self.login(self.student)
        data = self.form_data(shop=self.annex.pk)
        del data["letter"], data["valid_id"], data["consent"]
        self.client.post(reverse("student_resubmit", args=[req.pk]), data)
        req.refresh_from_db()
        self.assertEqual((req.status, req.shop), (Status.SUBMITTED, self.annex))

    def test_student_and_reviewers_see_the_branch(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            req = services.create_request(self.student, {"reason": "Lost", "date_lost": date(2026, 1, 5),
                                                         "place_lost": "Gym", "shop": self.main},
                                          {Upload.Kind.LETTER: pdf(), Upload.Kind.VALID_ID: png()})
        self.assertIn("ID shop: Main campus, Main building, Lucena City", mail.outbox[-1].body)
        self.login(self.student)
        resp = self.client.get(reverse("student_detail", args=[req.pk]))
        self.assertContains(resp, "Your ID shop")
        self.assertContains(resp, "https://www.google.com/maps/search/?api=1&amp;query=13.941400%2C121.623400")
        self.login(self.ph)
        self.assertContains(self.client.get(reverse("ph_detail", args=[req.pk])), "Main campus")
        ReplacementRequest.objects.filter(pk=req.pk).update(status=Status.OSAS_APPROVED)
        self.login(self.student)
        self.assertContains(self.client.get(reverse("authorization", args=[req.pk])), "Main building, Lucena City")

    def test_staff_set_their_own_branch_location(self):
        self.login(self.shop_b)
        url = reverse("shop_branch")
        self.assertContains(self.client.get(url), "Annex")
        # A short share link from the Google Maps app: saved as is, opened as is (no coordinates in it).
        short = "https://maps.app.goo.gl/AbCd1234"
        resp = self.client.post(url, {"maps_link": short, "address": "", "hours": "Mon to Fri"})
        self.assertIn("Branch location saved", self.messages_of(resp))
        self.annex.refresh_from_db()
        self.assertEqual((self.annex.maps_link, self.annex.latitude, self.annex.maps_url), (short, None, short))
        self.assertIsNotNone(self.annex.location_updated_at)
        # A full link: the dropped pin's coordinates are read from it.
        full = ("https://www.google.com/maps/place/DLL/@13.9412,121.6170,17z/data=!3m1!4b1!4m6!3m5"
                "!1s0x0:0x1!8m2!3d13.941234!4d121.617890")
        self.client.post(url, {"maps_link": full, "address": "Annex hall, Lucena"})
        self.annex.refresh_from_db()
        self.assertEqual((self.annex.latitude, self.annex.longitude), (Decimal("13.941234"), Decimal("121.617890")))
        self.main.refresh_from_db()
        self.assertEqual(self.main.address, "Main building, Lucena City")  # only their own branch
        # Not a Google Maps link, or nothing at all: refused, nothing changes.
        for bad, error in (({"maps_link": "https://evil.example/maps/@1,2"}, "Paste a Google Maps link"),
                           ({"maps_link": "https://google.evil.io/maps"}, "Paste a Google Maps link"),
                           ({"maps_link": "", "address": ""}, "Paste the Google Maps link of the branch")):
            resp = self.client.post(url, {"address": "", **bad})
            self.assertContains(resp, error)
            self.annex.refresh_from_db()
            self.assertEqual(self.annex.maps_link, full)
        for user in (self.student, self.osas, self.admin_user):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
        nobody = self.make_user("shop-none", roles.ID_SHOP)
        self.login(nobody)
        self.assertRedirects(self.client.get(url), reverse("shop_list"))

    def test_admin_adds_and_renames_branches(self):
        self.login(self.admin_user)
        resp = self.client.get(reverse("manage_shops"))
        self.assertContains(resp, "Main building, Lucena City")
        self.assertContains(resp, self.shop_b.display_name)
        resp = self.client.post(reverse("manage_shop_add"), {"name": "  Night campus "})
        self.assertIn("ID shop added", self.messages_of(resp))
        self.assertTrue(IdShop.objects.filter(name="Night campus").exists())
        resp = self.client.post(reverse("manage_shop_add"), {"name": "annex"})
        self.assertIn("An ID shop with this name already exists.", self.messages_of(resp))
        resp = self.client.post(reverse("manage_shop_rename", args=[self.annex.pk]), {"name": "Annex building"})
        self.assertIn("ID shop renamed", self.messages_of(resp))
        self.annex.refresh_from_db()
        self.assertEqual(self.annex.name, "Annex building")
        self.login(self.shop)
        self.assertEqual(self.client.get(reverse("manage_shops")).status_code, 403)

    def test_id_shop_staff_need_a_branch(self):
        self.login(self.admin_user)
        data = {"username": "shop-c", "full_name": "New Shop", "email": "shop-c@example.com", "role": roles.ID_SHOP,
                "password1": "Strong-pass-981", "password2": "Strong-pass-981"}
        resp = self.client.post(reverse("manage_staff_new"), data)
        self.assertContains(resp, "Choose the ID shop branch they work at.")
        self.client.post(reverse("manage_staff_new"), {**data, "shop": self.annex.pk})
        self.assertEqual(User.objects.get(username="shop-c").shop, self.annex)
        self.client.post(reverse("manage_staff_new"), {**data, "username": "osas-2", "email": "o2@example.com",
                                                       "role": roles.OSAS, "shop": self.annex.pk})
        self.assertIsNone(User.objects.get(username="osas-2").shop)

    def test_moving_staff_between_branches_and_out_of_the_role(self):
        self.login(self.admin_user)
        url = reverse("manage_user_edit", args=[self.shop_b.pk])
        base = {"full_name": self.shop_b.full_name, "email": self.shop_b.email}
        self.client.post(url, {**base, "role": roles.ID_SHOP, "shop": self.main.pk})
        self.shop_b.refresh_from_db()
        self.assertEqual(self.shop_b.shop, self.main)
        resp = self.client.post(url, {**base, "role": roles.ID_SHOP, "shop": ""})
        self.assertContains(resp, "Choose the ID shop branch they work at.")
        self.client.post(url, {**base, "role": roles.OSAS, "shop": self.main.pk})
        self.shop_b.refresh_from_db()
        self.assertIsNone(self.shop_b.shop)


class SessionExpiryTests(PortalTestCase):
    def test_expired_session_shows_message(self):
        self.client.cookies["sessionid"] = "expired-session-key"
        resp = self.client.get(reverse("student_list"), follow=True)
        self.assertContains(resp, "Your session expired. Log in again.")

    def test_no_message_for_fresh_visitor(self):
        resp = self.client.get(reverse("student_list"), follow=True)
        self.assertNotContains(resp, "Your session expired")


class PageSmokeTests(PortalTestCase):
    def test_remaining_pages_render(self):
        self.assertContains(self.client.get(reverse("signup")), "Create your account?")
        req = self.make_request(status=Status.PRINTED)
        self.login(self.shop)
        resp = self.client.get(reverse("shop_detail", args=[req.pk]))
        self.assertContains(resp, f"Release the ID to {self.student.full_name}?")
        self.assertContains(resp, "This closes the request.")
        self.login(self.admin_user)
        self.assertContains(self.client.get(reverse("manage_departments")), "Computer Studies")
        self.assertContains(self.client.get(reverse("manage_staff_new")), "Create this staff account?")
        self.assertContains(self.client.get(reverse("manage_users")), "Deactivate this account?")
        self.login(self.student)
        resp = self.client.get(reverse("osas_queue"))
        self.assertContains(resp, "You don't have access to this page", status_code=403)

    def test_rejected_tracker_marks_the_rejecting_step(self):
        req = self.make_request(status=Status.PH_APPROVED)
        ApprovalLog.objects.create(request=req, actor=self.ph, action=ApprovalLog.Action.PH_APPROVED)
        services.change_status(req, Status.REJECTED, self.osas, remarks="Wrong ID")
        req.refresh_from_db()
        steps = req.tracker_steps()
        self.assertEqual([s["state"] for s in steps][:3], ["done", "done", "rejected"])
        # Finished and rejected steps carry the date they happened; later steps don't.
        self.assertIsNotNone(steps[0]["date"])
        self.assertIsNotNone(steps[2]["date"])
        self.assertIsNone(steps[3]["date"])


class TemplateTextTests(PortalTestCase):
    """The modal texts from the brief are on the pages."""

    def test_modal_texts(self):
        self.login(self.student)
        self.assertContains(self.client.get(reverse("student_new")), "Submit your request?")
        req = self.make_request()
        self.assertContains(self.client.get(reverse("student_detail", args=[req.pk])), "Cancel this request?")
        self.login(self.ph)
        resp = self.client.get(reverse("ph_detail", args=[req.pk]))
        self.assertContains(resp, "Approve this request?")
        self.assertContains(resp, 'name="remarks"')
        services.change_status(req, Status.PH_APPROVED, self.ph)
        self.login(self.osas)
        self.assertContains(self.client.get(reverse("osas_detail", args=[req.pk])), "Approve and create the authorization?")

    def test_no_please_or_successfully(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent
        files = list(root.glob("*.py")) + list(root.glob("templates/**/*.html")) + list((root.parent / "templates").glob("**/*.html"))
        for f in files:
            if f.name == "tests.py":
                continue
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\b(please|successfully)\b", text, re.I), f.name)


class ArchiveTests(PortalTestCase):
    """Cancelled requests, departments and ID shop branches move to an Archived tab instead of being deleted."""

    def test_cancelled_requests_move_to_the_students_archive(self):
        done = self.make_request(status=Status.CANCELLED)
        live = self.make_request()
        self.login(self.student)
        resp = self.client.get(reverse("student_list"))
        self.assertContains(resp, live.reference_no)
        self.assertNotContains(resp, done.reference_no)
        self.assertContains(resp, "Archived")
        resp = self.client.get(reverse("student_list") + "?show=archived")
        self.assertContains(resp, reverse("student_detail", args=[done.pk]))
        self.assertNotContains(resp, reverse("student_detail", args=[live.pk]))

    def test_released_requests_are_archived_too_with_a_filter(self):
        released = self.make_request(status=Status.RELEASED)
        cancelled = self.make_request(status=Status.CANCELLED)
        self.login(self.student)
        link = lambda r: reverse("student_detail", args=[r.pk])
        self.assertNotContains(self.client.get(reverse("student_list")), link(released))
        base = reverse("student_list") + "?show=archived"
        resp = self.client.get(base)
        self.assertContains(resp, link(released))
        self.assertContains(resp, link(cancelled))
        resp = self.client.get(base + "&status=released")
        self.assertContains(resp, link(released))
        self.assertNotContains(resp, link(cancelled))
        resp = self.client.get(base + "&status=cancelled")
        self.assertNotContains(resp, link(released))
        self.assertContains(resp, link(cancelled))

    def test_osas_sees_cancelled_requests_on_their_own(self):
        done = self.make_request(status=Status.CANCELLED)
        self.login(self.osas)
        self.assertNotContains(self.client.get(reverse("osas_queue")), done.reference_no)
        self.assertContains(self.client.get(reverse("osas_queue") + "?show=cancelled"), done.reference_no)

    def test_archived_department_leaves_signup_but_keeps_its_students(self):
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_department_archive", args=[self.ba.pk]))
        self.assertIn("Department archived", self.messages_of(resp))
        self.ba.refresh_from_db()
        self.assertFalse(self.ba.is_active)
        self.assertEqual(self.ba.program_head, self.ph_other)  # requests still get reviewed
        self.client.get(reverse("manage_departments"))  # shows the messages above
        self.assertNotContains(self.client.get(reverse("manage_departments")), "Business Administration")
        self.assertContains(self.client.get(reverse("manage_departments") + "?show=archived"), "Business Administration")
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("signup")), "Business Administration")
        self.other_student.refresh_from_db()
        self.assertEqual(self.other_student.department, self.ba)
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_department_reactivate", args=[self.ba.pk]))
        self.assertIn("Department reactivated", self.messages_of(resp))
        self.ba.refresh_from_db()
        self.assertTrue(self.ba.is_active)

    def test_editing_a_head_keeps_their_archived_department(self):
        Department.objects.filter(pk=self.ba.pk).update(is_active=False)
        self.login(self.admin_user)
        url = reverse("manage_user_edit", args=[self.ph_other.pk])
        self.assertContains(self.client.get(url), "Business Administration")
        self.client.post(url, {"full_name": "PH BA", "email": self.ph_other.email, "role": roles.PROGRAM_HEAD,
                               "departments": [self.ba.pk]})
        self.ba.refresh_from_db()
        self.assertEqual(self.ba.program_head, self.ph_other)

    def test_archived_branch_is_not_offered_but_finishes_its_requests(self):
        main = IdShop.objects.create(name="Main campus")
        self.shop.shop = main
        self.shop.save()
        req = self.make_request(status=Status.OSAS_APPROVED)
        ReplacementRequest.objects.filter(pk=req.pk).update(shop=main)
        self.login(self.admin_user)
        resp = self.client.post(reverse("manage_shop_archive", args=[main.pk]))
        self.assertIn("ID shop archived", self.messages_of(resp))
        self.assertContains(self.client.get(reverse("manage_shops") + "?show=archived"), "Main campus")
        self.assertNotIn(main, list(services.open_shops()))
        self.login(self.other_student)
        self.assertNotContains(self.client.get(reverse("student_new")), 'name="shop"')
        self.login(self.shop)
        self.assertContains(self.client.get(reverse("shop_list")), "is archived")
        self.client.post(reverse("shop_payment", args=[req.pk]), {"amount": "150.00", "receipt_no": ""})
        req.refresh_from_db()
        self.assertEqual(req.status, Status.PAID)
        self.login(self.admin_user)
        self.client.post(reverse("manage_shop_reactivate", args=[main.pk]))
        self.assertIn(main, list(services.open_shops()))
        for name in ("manage_shop_archive", "manage_department_archive"):
            self.login(self.osas)
            self.assertEqual(self.client.post(reverse(name, args=[main.pk])).status_code, 403)


class SecurityTests(PortalTestCase):
    def test_login_pauses_after_too_many_wrong_passwords(self):
        url = reverse("login")
        for _ in range(5):
            resp = self.client.post(url, {"username": self.student.username, "password": "wrong"})
            self.assertContains(resp, "Wrong student ID or password.")
        # Now even the right password is refused for a while, with a plain message.
        resp = self.client.post(url, {"username": self.student.username.upper(), "password": PASSWORD})
        self.assertContains(resp, "Too many wrong passwords for this account.")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Other accounts aren't affected.
        resp = self.client.post(url, {"username": self.osas.username, "password": PASSWORD})
        self.assertEqual(resp.status_code, 302)

    def test_a_right_password_resets_the_count(self):
        url = reverse("login")
        for _ in range(4):
            self.client.post(url, {"username": self.student.username, "password": "wrong"})
        self.assertEqual(self.client.post(url, {"username": self.student.username, "password": PASSWORD}).status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.client.post(url, {"username": self.student.username, "password": "wrong"})
        self.assertEqual(self.client.post(url, {"username": self.student.username, "password": PASSWORD}).status_code, 302)

    def test_pages_send_a_content_security_policy(self):
        csp = self.client.get(reverse("login")).headers["Content-Security-Policy"]
        self.assertIn("script-src 'self' https://cdn.jsdelivr.net", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertNotIn("onerror=", self.client.get(reverse("login")).content.decode())

    def test_uploads_are_served_without_the_page_policy(self):
        req = self.make_request()
        self.login(self.student)
        resp = self.client.get(reverse("upload_file", args=[req.uploads.first().pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Content-Security-Policy", resp.headers)
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")


class PasswordTests(PortalTestCase):
    def test_user_changes_their_own_password(self):
        self.login(self.osas)
        url = reverse("password_change")
        self.assertContains(self.client.get(url), "Change your password?")
        resp = self.client.post(url, {"old_password": "wrong", "new_password1": "New-pass-4471",
                                      "new_password2": "New-pass-4471"})
        self.assertContains(resp, "That isn&#x27;t your current password.")
        resp = self.client.post(url, {"old_password": PASSWORD, "new_password1": "New-pass-4471",
                                      "new_password2": "New-pass-4471"})
        self.assertIn("Password changed", self.messages_of(resp))
        self.assertEqual(self.client.get(reverse("osas_queue")).status_code, 200)  # still logged in here
        self.osas.refresh_from_db()
        self.assertTrue(self.osas.check_password("New-pass-4471"))
        self.client.logout()
        self.assertRedirects(self.client.get(url), reverse("login") + "?next=" + url)

    def test_forgot_password_by_student_id_or_email(self):
        mail.outbox.clear()
        for value in (self.student.username.upper(), self.student.email.upper()):
            resp = self.client.post(reverse("password_reset"), {"email": value})
            self.assertRedirects(resp, reverse("password_reset_sent"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, [self.student.email])
        self.assertEqual(mail.outbox[0].subject, "Reset your Quik RE-ID password")
        # Nobody by that name: same page, no email (doesn't reveal who has an account).
        resp = self.client.post(reverse("password_reset"), {"email": "nobody-here"})
        self.assertRedirects(resp, reverse("password_reset_sent"))
        self.assertEqual(len(mail.outbox), 2)
        # The link sets a new password once, then stops working.
        link = re.search(r"https?://[^/]+(/password-reset/\S+/)", mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.assertContains(page, "Set a new password")
        resp = self.client.post(page.redirect_chain[-1][0], {"new_password1": "Fresh-pass-8832",
                                                             "new_password2": "Fresh-pass-8832"})
        self.assertRedirects(resp, reverse("login"))
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("Fresh-pass-8832"))
        self.assertContains(self.client.get(link, follow=True), "This link no longer works")

    def test_reset_emails_are_limited_and_end_a_login_pause(self):
        mail.outbox.clear()
        for _ in range(5):
            self.client.post(reverse("password_reset"), {"email": self.ph.username})
        self.assertEqual(len(mail.outbox), 3)   # at most 3 an hour per account
        for _ in range(5):
            self.client.post(reverse("login"), {"username": self.ph.username, "password": "wrong"})
        link = re.search(r"https?://[^/]+(/password-reset/\S+/)", mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.client.post(page.redirect_chain[-1][0], {"new_password1": "Fresh-pass-8832",
                                                      "new_password2": "Fresh-pass-8832"})
        resp = self.client.post(reverse("login"), {"username": self.ph.username, "password": "Fresh-pass-8832"})
        self.assertEqual(resp.status_code, 302)   # the new password works right away

    def test_login_page_links_to_forgot_password(self):
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))


class ActivityLogTests(PortalTestCase):
    """FR-18: every action lands in the activity log with who, what and when."""

    def entries(self, **filters):
        return list(AuditLog.objects.filter(**filters).values_list("action", flat=True))

    def test_sign_in_events_are_logged(self):
        self.client.post(reverse("login"), {"username": self.osas.username, "password": "wrong"})
        self.client.post(reverse("login"), {"username": self.osas.username, "password": PASSWORD})
        self.client.post(reverse("logout"))
        self.assertEqual(self.entries(kind="sign_in")[::-1], ["Wrong password", "Logged in", "Logged out"])
        ok = AuditLog.objects.get(action="Logged in")
        self.assertEqual((ok.actor, ok.ip), (self.osas, "127.0.0.1"))

    def test_status_changes_and_file_views_are_logged(self):
        req = self.make_request()
        self.login(self.ph)
        self.client.post(reverse("ph_reject", args=[req.pk]), {"remarks": "Unsigned letter"})
        e = AuditLog.objects.filter(kind="request").first()
        self.assertEqual((e.action, e.actor, e.request), ("Rejected", self.ph, req))
        self.assertIn("Unsigned letter", e.summary)
        self.client.get(reverse("upload_file", args=[req.uploads.first().pk]))
        self.assertEqual(AuditLog.objects.filter(kind="file").first().actor, self.ph)

    def test_admin_actions_are_logged_and_the_page_is_admin_only(self):
        self.login(self.admin_user)
        self.client.post(reverse("manage_deactivate", args=[self.osas.pk]))
        self.client.post(reverse("manage_department_rename", args=[self.ba.pk]), {"name": "Business"})
        self.client.post(reverse("manage_shop_add"), {"name": "Annex"})
        self.assertEqual(sorted(self.entries(actor=self.admin_user, kind__in=["account", "department", "branch"])),
                         ["Added branch", "Deactivated account", "Renamed department"])
        self.assertIn("Business Administration to Business", AuditLog.objects.get(action="Renamed department").summary)
        resp = self.client.get(reverse("manage_activity") + "?kind=department")
        self.assertContains(resp, "Renamed department")
        self.assertNotContains(resp, "Added branch")
        self.assertContains(self.client.get(reverse("manage_activity") + "?q=Annex"), "Added branch")
        self.login(self.ph)
        self.assertEqual(self.client.get(reverse("manage_activity")).status_code, 403)

    def test_entries_cannot_be_changed(self):
        self.login(self.admin_user)
        self.client.post(reverse("manage_shop_add"), {"name": "Annex"})
        e = AuditLog.objects.get(action="Added branch")
        e.summary = "something else"
        with self.assertRaises(ValueError):
            e.save()

    def test_proxy_ip_uses_the_last_forwarded_address(self):
        self.client.post(reverse("login"), {"username": self.osas.username, "password": PASSWORD},
                         HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9")
        self.assertEqual(AuditLog.objects.get(action="Logged in").ip, "203.0.113.9")


class VisitCalendarTests(PortalTestCase):
    """FR-16: students book a morning or afternoon visit at their branch; the branch sets the calendar."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main = IdShop.objects.create(name="Main campus", slot_capacity=2, book_days_ahead=2)
        cls.annex = IdShop.objects.create(name="Annex")
        cls.shop.shop = cls.main
        cls.shop.save()
        cls.shop_b = cls.make_user("shop-b", roles.ID_SHOP)
        cls.shop_b.shop = cls.annex
        cls.shop_b.save()

    def setUp(self):
        super().setUp()
        # Monday Nov 23, 2026, 9:05 AM (Manila). Main campus is open Mondays 8:00 AM to 5:00 PM.
        self.now = timezone.make_aware(datetime(2026, 11, 23, 9, 5))
        BranchHours.objects.create(shop=self.main, weekday=0, opens=dt_time(8), closes=dt_time(17))

    def at(self, h, m=0, day=23):
        return timezone.make_aware(datetime(2026, 11, day, h, m))

    def approved(self, student=None, shop=None):
        req = self.make_request(student=student, status=Status.OSAS_APPROVED)
        ReplacementRequest.objects.filter(pk=req.pk).update(shop=shop or self.main)
        req.refresh_from_db()
        return req

    def test_days_split_into_morning_and_afternoon(self):
        days = services.visit_slots(self.main, self.now)
        self.assertEqual(len(days), 1)   # Tuesday (inside the 2-day window) has no hours
        slots = days[0]["slots"]
        self.assertEqual([s["period"] for s in slots], ["Morning", "Afternoon"])
        self.assertEqual([(s["start"], s["end"]) for s in slots],
                         [(self.at(8), self.at(12)), (self.at(12), self.at(17))])   # this morning is still bookable
        self.assertEqual(services.visit_slots(self.main, self.at(12, 30))[0]["slots"][0]["period"], "Afternoon")
        BranchHours.objects.create(shop=self.main, weekday=1, opens=dt_time(13), closes=dt_time(17))
        tuesday = services.visit_slots(self.main, self.now)[1]["slots"]
        self.assertEqual([(s["period"], s["start"]) for s in tuesday], [("Afternoon", self.at(13, day=24))])
        services.add_closure(self.main, self.now.date(), "Holiday", self.shop)
        self.assertEqual([d["date"] for d in services.visit_slots(self.main, self.now)], [self.at(13, day=24).date()])
        self.assertEqual(services.visit_slots(self.annex, self.now), [])   # no hours = walk-in only

    def test_booking_respects_capacity_and_can_move(self):
        a, b, c = self.approved(), self.approved(), self.approved()
        services.book_visit(a, self.student, self.at(8), now=self.now)
        services.book_visit(b, self.student, self.at(8), now=self.now)
        with self.assertRaisesMessage(services.TransitionError, "That time is full."):
            services.book_visit(c, self.student, self.at(8), now=self.now)
        with self.assertRaisesMessage(services.TransitionError, "open anymore"):
            services.book_visit(c, self.student, self.at(9), now=self.now)   # not the start of a half-day
        services.book_visit(a, self.student, self.at(12), now=self.now)     # moving frees a morning place
        services.book_visit(c, self.student, self.at(8), now=self.now)
        left = {s["period"]: s["left"] for s in services.visit_slots(self.main, self.now)[0]["slots"]}
        self.assertEqual(left, {"Morning": 0, "Afternoon": 1})
        a.refresh_from_db()
        self.assertEqual((a.visit_period, a.visit_end), ("Afternoon", self.at(17)))
        self.assertEqual(AuditLog.objects.filter(action__in=["Booked visit", "Moved visit"]).count(), 4)

    def test_only_your_own_waiting_to_pay_request_can_book(self):
        req = self.make_request()   # still Submitted
        ReplacementRequest.objects.filter(pk=req.pk).update(shop=self.main)
        req.refresh_from_db()
        with self.assertRaises(services.TransitionError):
            services.book_visit(req, self.student, self.at(8), now=self.now)
        mine = self.approved()
        with self.assertRaises(PermissionDenied):
            services.book_visit(mine, self.other_student, self.at(8), now=self.now)

    def test_missed_visit_can_be_rebooked(self):
        req = self.approved()
        yesterday_morning = timezone.localtime(timezone.now() - timedelta(days=1)).replace(hour=8, minute=0)
        ReplacementRequest.objects.filter(pk=req.pk).update(visit_at=yesterday_morning)
        req.refresh_from_db()
        self.assertTrue(req.visit_missed)
        self.assertFalse(req.visit_upcoming)
        services.book_visit(req, self.student, self.at(12), now=self.now)
        req.refresh_from_db()
        self.assertEqual(req.visit_at, self.at(12))

    def test_closing_a_day_clears_its_bookings_and_emails_them(self):
        req = self.approved()
        services.book_visit(req, self.student, self.at(12), now=self.now)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            cleared = services.add_closure(self.main, self.now.date(), "Holiday", self.shop)
        self.assertEqual(cleared, 1)
        req.refresh_from_db()
        self.assertIsNone(req.visit_at)
        self.assertIn("pick a new visit time", mail.outbox[-1].subject)

    def test_student_books_from_their_request_page(self):
        BranchHours.objects.filter(shop=self.main).delete()
        BranchHours.objects.bulk_create([BranchHours(shop=self.main, weekday=d, opens=dt_time(8),
                                                     closes=dt_time(17)) for d in range(7)])
        req = self.approved()
        self.login(self.student)
        page = self.client.get(reverse("student_detail", args=[req.pk]))
        self.assertContains(page, "Your visit")
        self.assertContains(page, "Afternoon")
        start = services.visit_slots(self.main, exclude=req.pk)[-1]["slots"][0]["start"]
        resp = self.client.post(reverse("student_visit", args=[req.pk]), {"slot": timezone.localtime(start).isoformat()})
        self.assertTrue(any(m.startswith("Visit booked:") and "morning" in m for m in self.messages_of(resp)))
        req.refresh_from_db()
        self.assertEqual(req.visit_at, start)
        self.assertContains(self.client.get(reverse("authorization", args=[req.pk])), "morning")
        self.client.post(reverse("student_visit_cancel", args=[req.pk]))
        req.refresh_from_db()
        self.assertIsNone(req.visit_at)
        self.login(self.other_student)
        resp = self.client.post(reverse("student_visit", args=[req.pk]), {"slot": start.isoformat()})
        self.assertEqual(resp.status_code, 403)

    def test_branch_staff_configure_their_own_calendar(self):
        self.login(self.shop)
        data = {"slot_capacity": "40", "book_days_ahead": "10",
                "open_1": "on", "opens_1": "08:00", "closes_1": "17:00",
                "open_2": "on", "opens_2": "13:00", "closes_2": "12:00"}   # closes before it opens
        self.assertContains(self.client.post(reverse("shop_calendar"), data), "Wednesday closes before it opens.")
        del data["open_2"]
        resp = self.client.post(reverse("shop_calendar"), data)
        self.assertIn("Calendar saved", self.messages_of(resp))
        self.main.refresh_from_db()
        self.assertEqual((self.main.slot_capacity, self.main.book_days_ahead), (40, 10))
        self.assertEqual(list(self.main.hours_set.values_list("weekday", flat=True)), [1])
        self.client.post(reverse("shop_closure_add"), {"date": "2099-12-25", "note": "Christmas"})
        closure = self.main.closures.get()
        self.login(self.shop_b)   # another branch can't remove it
        self.assertEqual(self.client.post(reverse("shop_closure_remove", args=[closure.pk])).status_code, 403)
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("shop_calendar")).status_code, 403)

    def test_shop_list_shows_todays_visits(self):
        req = self.approved()
        ReplacementRequest.objects.filter(pk=req.pk).update(visit_at=timezone.now())
        self.login(self.shop)
        resp = self.client.get(reverse("shop_list"))
        self.assertContains(resp, "visits")
        self.assertContains(resp, req.reference_no)
        self.login(self.shop_b)   # another branch doesn't see it
        self.assertNotContains(self.client.get(reverse("shop_list")), req.reference_no)
