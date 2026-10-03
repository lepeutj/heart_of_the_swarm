class TriggerNotFoundError(ValueError):
    """Raised when an invocation references an unknown trigger."""


class TriggerDisabledError(ValueError):
    """Raised when an invocation references a disabled trigger."""


class TriggerInvocationError(ValueError):
    """Raised when a trigger cannot invoke the requested target runtime."""
