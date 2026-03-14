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

    # Sustainability
    entsoe_token: str | None = None  # Free real-time for EU regions (register at transparency@entsoe.eu)
    eia_api_key: str | None = None  # Free real-time for US regions (register at https://www.eia.gov/opendata/)

    # Sustainability scoring
    # Max fractional score boost from sustainability when use_sustainability=True (e.g. 0.15 = 15%)
    sustainability_boost_max: float = 0.15
    # Cache settings
    sustainability_cache_ttl_seconds: int = 900  # 15 minutes
    sustainability_cache_max_entries: int = 500  # Prevent unbounded growth

    # ── Cloud provider credentials (for ingestion) ─────────────────────────
    # Exoscale — https://portal.exoscale.com/u/{org}/iam/api-keys
    exoscale_api_key: str | None = None
    exoscale_api_secret: str | None = None


    # Aruba Cloud — account username + password for Aruba Cloud REST API
    aruba_username: str | None = None
    aruba_password: str | None = None
    aruba_dc_id: int = 1  # Data center ID (1=ITBG-1, 2=ITBG-2, 3=ITBG-3)

    # IBM Cloud — API key for VPC and Global Catalog API (create at https://cloud.ibm.com/iam/apikeys)
    ibmcloud_api_key: str | None = None


settings = Settings()
