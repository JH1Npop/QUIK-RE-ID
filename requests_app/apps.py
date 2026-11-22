from django.apps import AppConfig


class RequestsAppConfig(AppConfig):
    name = 'requests_app'

    def ready(self):
        from . import signals  # noqa: F401  (activity log hooks)
