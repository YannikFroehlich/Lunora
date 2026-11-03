from django.apps import AppConfig


class AppConfig(AppConfig):
    name = "app"

    def ready(self):
        from app.services.audit_log import connect_signals

        connect_signals()
