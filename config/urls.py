from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from requests_app.forms import LoginForm

admin.site.site_header = "Quik RE-ID back office"
admin.site.site_title = "Quik RE-ID back office"
# Only active superusers may use the Django admin, even if someone else has is_staff.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True, authentication_form=LoginForm),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("requests_app.urls")),
]

# The Django admin is a back-office tool, not part of the portal. Day-to-day admin work
# happens on the portal's own Accounts and Departments pages. It is only mounted when
# ADMIN_URL is set in .env, at that hard-to-guess path, and nothing links to it.
if settings.ADMIN_URL:
    urlpatterns.insert(0, path(settings.ADMIN_URL, admin.site.urls))
