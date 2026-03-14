"""Domain-specific exceptions for CloudBroker."""


class CloudBrokerError(Exception):
    """Base exception for all CloudBroker domain errors."""


class IngestionError(CloudBrokerError):
    """Raised when data ingestion from a provider fails."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class RecommendationError(CloudBrokerError):
    """Raised when the recommendation engine encounters an unrecoverable error."""


class ProviderUnavailableError(CloudBrokerError):
    """Raised when a provider API is unreachable or returns persistent errors."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' unavailable: {message}")
