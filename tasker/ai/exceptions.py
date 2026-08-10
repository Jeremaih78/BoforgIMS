class AIServiceError(RuntimeError):
    """Base error safe for application-layer handling."""

    code = "ai_service_error"
    user_message = "The AI service could not complete the request."


class AIDisabledError(AIServiceError):
    code = "ai_disabled"
    user_message = "Boforg AI is currently disabled."


class AIPermissionError(AIServiceError):
    code = "ai_permission_denied"
    user_message = "You do not have permission to use Boforg AI."


class AIConfigurationError(AIServiceError):
    code = "ai_configuration_error"
    user_message = "Boforg AI is not configured correctly."


class AIPromptError(AIServiceError):
    code = "ai_prompt_error"
    user_message = "The requested AI prompt is unavailable or invalid."


class AIResponseValidationError(AIServiceError):
    code = "ai_response_invalid"
    user_message = "Boforg AI returned an invalid response. Please try again."


class AIRateLimitError(AIServiceError):
    code = "ai_rate_limited"
    user_message = "Your AI usage limit has been reached. Please try again later."

    def __init__(self, message=None, *, retry_after=None):
        super().__init__(message or self.user_message)
        self.retry_after = retry_after


class AIProviderError(AIServiceError):
    code = "ai_provider_error"

    def __init__(
        self,
        message=None,
        *,
        transient=False,
        provider_code="",
        request_id="",
        status_code=None,
        retry_after=None,
    ):
        super().__init__(message or self.user_message)
        self.transient = transient
        self.provider_code = provider_code
        self.request_id = request_id
        self.status_code = status_code
        self.retry_after = retry_after


class AITimeoutError(AIProviderError):
    code = "ai_timeout"
    user_message = "The AI service took too long to respond."

    def __init__(self, **kwargs):
        super().__init__(self.user_message, transient=True, **kwargs)


class AIStreamInterruptedError(AIProviderError):
    code = "ai_stream_interrupted"
    user_message = "The AI response was interrupted before it finished."
