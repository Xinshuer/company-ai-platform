"""Gateway errors. Messages are fixed, short and say what to do; they never quote message text,
backend responses or API keys (backend error bodies can echo part of the key)."""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for everything the gateway raises on purpose."""


class LocalUnavailable(GatewayError):
    """The local route is switched off. L3/L4 requests error out; they never go external."""

    def __init__(self, msg: str = "高敏模型维护中 / the local model is under maintenance; try again later") -> None:
        super().__init__(msg)


class ExternalUnavailable(GatewayError):
    """The external route was switched off while this call waited for a slot or between retries.

    The request is not moved to local at that point: its egress row and budget reservation were
    made for the external route. The caller retries and the new call is routed afresh.
    """

    def __init__(self, msg: str = "外部模型已暂停 / the external model was switched off; try again") -> None:
        super().__init__(msg)


class SecretDetected(GatewayError):
    """A message still contained a secret after scrubbing. Rule 5: refuse rather than send it."""

    def __init__(self, msg: str = "消息里有密钥，已拒绝发送 / the message contains a credential; remove it and "
                                  "try again") -> None:
        super().__init__(msg)


class GatewayBusy(GatewayError):
    """No concurrency slot became free within queue_timeout_s."""


class BudgetExceeded(GatewayError):
    """The department's token budget for the day (UTC) is used up."""


class BackendError(GatewayError):
    """The model backend failed after retries. Carries only the route, an error class and a status."""

    def __init__(self, msg: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(msg)
        self.status = status
        self.retryable = retryable


class RetryableBackendError(BackendError):
    """429, 5xx, timeouts and connection errors: the gateway retries these with backoff."""

    def __init__(self, msg: str, *, status: int | None = None) -> None:
        super().__init__(msg, status=status, retryable=True)


class MissingApiKey(GatewayError):
    """Live mode without the key in the environment. Names the variable, never a value."""


class EgressDenied(GatewayError):
    """A destination, host or level that config/egress.yaml does not allow."""
