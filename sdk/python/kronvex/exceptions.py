class KronvexError(Exception):
    """Base exception for all Kronvex SDK errors."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(KronvexError):
    """Invalid or missing API key."""


class RateLimitError(KronvexError):
    """Too many requests."""


class MemoryLimitError(KronvexError):
    """Memory quota exceeded for this plan."""


class AgentNotFoundError(KronvexError):
    """Agent does not exist or belongs to another key."""


class ServiceUnavailableError(KronvexError):
    """Embedding service temporarily unavailable (503). Safe to retry."""
