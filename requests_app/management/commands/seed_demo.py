"""Create 2 departments and one demo user per role.

    python manage.py seed_demo
    python manage.py seed_demo --password "your-demo-password" --student-email you@example.com

No password is stored in the code: it comes from --password, the SEED_DEMO_PASSWORD
environment variable, or a random one printed at the end. Running it again resets
nothing for existing users unless a password is given, which resets the demo users' passwords.
"""
import os

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.crypto import get_random_string

from requests_app import roles
from requests_app.models import Department, User

DEMO_LRN = "100000000001"  # sample 12-digit LRN for the demo student
DEPARTMENTS = ["College of Computer Studies", "College of Business Administration"]
DEMO_USERS = [
    # username, full name, role, email
    ("2026-00001", "Demo Student", roles.STUDENT, "student@example.com"),
    ("programhead", "Demo Program Head", roles.PROGRAM_HEAD, "programhead@example.com"),
    ("osas", "Demo OSAS Officer", roles.OSAS, "osas@example.com"),
    ("idshop", "Demo ID Shop Staff", roles.ID_SHOP, "idshop@example.com"),
    ("portaladmin", "Demo Admin", roles.ADMIN, "admin@example.com"),
]


class Command(BaseCommand):
    help = "Create 2 departments and one demo user per role."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for every demo user.")
        parser.add_argument("--student-email", help="Email for the demo student, to receive the status emails.")

    @transaction.atomic
    def handle(self, *args, **opts):
        explicit_password = opts["password"] or os.environ.get("SEED_DEMO_PASSWORD")
        password = explicit_password or get_random_string(14)
        groups = {name: Group.objects.get_or_create(name=name)[0] for name in roles.ALL_ROLES}
        depts = [Department.objects.get_or_create(name=name)[0] for name in DEPARTMENTS]

        for username, full_name, role, email in DEMO_USERS:
            if role == roles.STUDENT and opts["student_email"]:
                email = opts["student_email"]
            user, created = User.objects.get_or_create(username=username, defaults={"full_name": full_name})
            user.full_name = user.full_name or full_name
            user.email = email
            user.is_active = True
            if role == roles.STUDENT:
                user.department = depts[0]
                if not user.lrn and not User.objects.filter(lrn=DEMO_LRN).exists():
                    user.lrn = DEMO_LRN
            if role == roles.ADMIN:
                user.is_staff = True
                user.is_superuser = True
            if created or explicit_password:
                user.set_password(password)
            user.save()
            user.groups.add(groups[role])
            if role == roles.PROGRAM_HEAD:
                depts[0].program_head = user
                depts[0].save(update_fields=["program_head"])
            note = "created" if created else ("password reset" if explicit_password else "already exists, password kept")
            self.stdout.write(f"{role:13} {username:12} {note}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDepartments: {', '.join(DEPARTMENTS)}"
            f"\nThe Program Head reviews {DEPARTMENTS[0]}. The demo student belongs to it."
        ))
        self.stdout.write(self.style.WARNING(
            f"Password for the users created or reset above: {password}"
        ))
