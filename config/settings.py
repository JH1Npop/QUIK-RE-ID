"""
Settings for the Quik RE-ID.

Every secret and environment-specific value is read from .env (see .env.example).
"""
import sys
from pathlib import Path

import environ
from django.contrib.messages import constants as message_constants
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    SESSION_COOKIE_AGE=(int, 60 * 60 * 2),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")
# On Render, trust the service's own address automatically (e.g. quik-re-id.onrender.com).
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # staticfiles comes first so Django's own collectstatic runs: static files are served by WhiteNoise, and
    # cloudinary_storage's replacement command breaks on Django 6 (it reads the removed STATICFILES_STORAGE).
    "django.contrib.staticfiles",
    "cloudinary_storage",
    "cloudinary",
    "requests_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "requests_app.audit.AuditMiddleware",   # lets the activity log see who acted and from which IP
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "requests_app.context_processors.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: PostgreSQL via psycopg, configured by DATABASE_URL in .env
DATABASES = {"default": env.db("DATABASE_URL")}
# Aiven may close an idle connection; check a connection before reusing it instead of failing the request.
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0 if DEBUG else 60)
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    # Same backend, plus a short connect timeout and retries (see config/pg_backend/base.py)
    DATABASES["default"]["ENGINE"] = "config.pg_backend"
    # A small shared connection pool (psycopg_pool). Opening a TLS connection to Aiven takes most of a second,
    # so reusing one is the biggest speed-up; the pool's size cap also keeps us inside Aiven's few connection
    # slots, even under runserver's thread-per-request. Pooling replaces CONN_MAX_AGE, which must be 0.
    # Not while running tests: the test runner holds each test in one long transaction, and a test that hits a
    # database error (e.g. a duplicate receipt) could then get a second pooled connection that waits on the first.
    running_tests = len(sys.argv) > 1 and sys.argv[1] == "test"
    if env.bool("DB_POOL", default=not running_tests):
        DATABASES["default"]["CONN_MAX_AGE"] = 0
        DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
            "min_size": 1,
            "max_size": env.int("DB_POOL_MAX", default=4),  # per server process
            "max_idle": 120,   # close spare connections before Aiven drops them
            "timeout": 20,     # wait this long for a free connection
        }

AUTH_USER_MODEL = "requests_app.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
# Password reset links work once and expire after an hour.
PASSWORD_RESET_TIMEOUT = 60 * 60
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Manila")
USE_I18N = True
USE_TZ = True

# Static files (WhiteNoise serves them in production)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Cloudinary (uploads). Every upload is private: see requests_app/storage.py.
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": env("CLOUDINARY_CLOUD_NAME", default=""),
    "API_KEY": env("CLOUDINARY_API_KEY", default=""),
    "API_SECRET": env("CLOUDINARY_API_SECRET", default=""),
    "MEDIA_TAG": "id-portal",
}

STORAGES = {
    "default": {"BACKEND": "requests_app.storage.PrivateCloudinaryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if not DEBUG:
    # Production: WhiteNoise serves gzip/brotli copies with hashed names, cached by browsers for a year.
    # Needs `python manage.py collectstatic` at deploy time.
    STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}

# Uploads are at most 5 MB each; keep the whole request body within a safe size.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# Django admin path, e.g. "backoffice-k3x9q2/". Empty = the Django admin is switched off.
ADMIN_URL = env("ADMIN_URL", default="").strip("/")
if ADMIN_URL:
    ADMIN_URL += "/"

# Used in status emails to link back to the portal, e.g. https://your-app.example.com
SITE_URL = env("SITE_URL", default="")

# Email via Brevo SMTP relay (Django send_mail)
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="smtp-relay.brevo.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"requests_app": {"handlers": ["console"], "level": "INFO"}},
}

# Bootstrap alert classes for Django messages
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

SESSION_COOKIE_AGE = env("SESSION_COOKIE_AGE")
# Sessions are read from a per-process memory cache first and from the database only on a miss:
# one less database round trip on every page. Logging out or deactivating an account still works.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# Production hardening when DEBUG is off
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Content Security Policy: the browser only runs scripts and loads styles, fonts and frames from this site
# and the two CDNs the pages use, so injected HTML can't run code. No inline scripts or on*="" handlers;
# inline style="" attributes are allowed. Uploaded files are served without it (views.upload_file).
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, "https://cdn.jsdelivr.net"],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://cdn.jsdelivr.net", "https://fonts.googleapis.com"],
    "font-src": [CSP.SELF, "https://fonts.gstatic.com"],
    "img-src": [CSP.SELF, "data:"],
    "frame-src": [CSP.SELF],
    # The CDN is listed so browser DevTools can load Bootstrap's source maps (otherwise a console warning).
    "connect-src": [CSP.SELF, "https://cdn.jsdelivr.net"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.SELF],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
