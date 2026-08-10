from tasker.models import AIConfiguration

from .exceptions import AIConfigurationError, AIDisabledError


class AIConfigurationManager:
    def current(self, *, require_enabled=True):
        configuration = AIConfiguration.objects.filter(is_active=True).first()
        if configuration is None:
            raise AIConfigurationError("No active AI configuration exists.")
        if require_enabled and not configuration.is_enabled:
            raise AIDisabledError()
        return configuration

