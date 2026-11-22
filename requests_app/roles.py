"""Role names (Django Groups) and the checks every view uses."""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

STUDENT = "Student"
PROGRAM_HEAD = "Program Head"
OSAS = "OSAS"
ID_SHOP = "ID Shop"
ADMIN = "Admin"
ALL_ROLES = [STUDENT, PROGRAM_HEAD, OSAS, ID_SHOP, ADMIN]
STAFF_ROLES = [PROGRAM_HEAD, OSAS, ID_SHOP, ADMIN]

SESSION_EXPIRED = "Your session expired. Log in again."


def role_names(user):
    if not user.is_authenticated:
        return set()
    if not hasattr(user, "_role_names"):
        names = set(user.groups.values_list("name", flat=True))
        if user.is_superuser:
            names.add(ADMIN)
        user._role_names = names
    return user._role_names


def has_role(user, *roles):
    return bool(role_names(user) & set(roles))


def primary_role(user):
    names = role_names(user)
    for role in (ADMIN, OSAS, PROGRAM_HEAD, ID_SHOP, STUDENT):
        if role in names:
            return role
    return None


def role_required(*roles):
    """Log-in check plus role check. No roles means any logged-in user.

    Anonymous users go to the login page (with a "session expired" note when they
    still carry an old session cookie). Logged-in users without the role get 403.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if settings.SESSION_COOKIE_NAME in request.COOKIES:
                    messages.info(request, SESSION_EXPIRED)
                return redirect_to_login(request.get_full_path())
            if roles and not has_role(request.user, *roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
