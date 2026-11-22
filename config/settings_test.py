"""Fast local test settings: in-memory SQLite instead of the hosted PostgreSQL.

    python manage.py test --settings=config.settings_test

Run the normal `python manage.py test` (PostgreSQL) before a release: SQLite
ignores select_for_update, so row locking is only really exercised there.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Tests don't run collectstatic, so never use the hashed-name (manifest) storage here.
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
