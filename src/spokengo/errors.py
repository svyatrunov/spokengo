"""Typed errors. Provider errors carry a ``transient`` flag so the pipeline can
decide whether to queue for retry (transient: network/429/5xx) or surface a
permanent failure immediately (bad model, bad key, file too large) — the latter
must NEVER be retried in a loop.
"""


class SpokenGoError(Exception):
    """Base for all app errors."""


class InvalidTransition(SpokenGoError):
    """Illegal state-machine transition (concurrency guard)."""


class ConfigError(SpokenGoError):
    """Broken or unreadable configuration."""


class AudioError(SpokenGoError):
    """Recording device / capture failure."""


class ProviderError(SpokenGoError):
    """Transcription provider failed. Permanent by default."""
    transient = False


class TransientProviderError(ProviderError):
    """Retryable failure (network, timeout, 429, 5xx)."""
    transient = True


class RateLimitError(TransientProviderError):
    """Provider rate limit hit (429)."""


class NetworkError(TransientProviderError):
    """Could not reach the provider (DNS, timeout, connection)."""


class AuthError(ProviderError):
    """Invalid or missing API key (401). Permanent."""


class FileTooLargeError(ProviderError):
    """Audio exceeds the provider size limit (413). Permanent."""


class BadRequestError(ProviderError):
    """Request rejected (400/404/422) — e.g. an unknown model id. Permanent."""


class InjectionError(SpokenGoError):
    """Could not insert text into the target field."""
