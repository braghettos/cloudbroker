"""Application configuration from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment (and .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cloudbroker"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/cloudbroker"

    # Security
    api_key: str | None = None  # Required in production, optional in development
    cors_origins: str = ""  # Comma-separated list of allowed origins
    rate_limit_per_minute: int = 100  # Rate limit per IP per minute

    # Recommendation engine defaults
    recommendation_price_weight: float = 0.5
    recommendation_fit_weight: float = 0.5
    recommendation_preference_multiplier: float = 1.2  # Score multiplier for preferred providers

    # Multi-criteria scoring weights (cost, performance, reliability)
    recommendation_cost_weight: float = 0.4
    recommendation_performance_weight: float = 0.3
    recommendation_reliability_weight: float = 0.3


settings = Settings()
