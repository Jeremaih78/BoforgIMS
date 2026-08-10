from django.apps import AppConfig


class TaskerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasker"
    verbose_name = "Boforg AI Tasker"

    def ready(self):
        from . import signals  # noqa: F401
