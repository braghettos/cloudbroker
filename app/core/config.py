"""Application configuration from environment variables and file-based secrets.

Credentials can be provided via:
  1. Environment variables (highest priority) — for local dev / docker-compose
  2. Secret files at SECRETS_DIR (default: /run/secrets/cloudbroker) — for Kubernetes
  3. .env file — for local dev fallback

In Kubernetes, mount a Secret as a volume at SECRETS_DIR. Each key in the Secret
becomes a file that Pydantic reads automatically (e.g., /run/secrets/cloudbroker/hetzner_api_token).
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets/cloudbroker")


class Settings(BaseSettings):
    """Settings loaded from environment, secret files, and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        secrets_dir=_SECRETS_DIR if Path(_SECRETS_DIR).is_dir() else None,
    )

    app_env: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cloudbroker"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/cloudbroker"

    # Security
    api_key: str | None = None
    cors_origins: str = ""
    rate_limit_per_minute: int = 100

    # Recommendation engine defaults
    recommendation_price_weight: float = 0.5
    recommendation_fit_weight: float = 0.5
    recommendation_preference_multiplier: float = 1.2

    # Multi-criteria scoring weights
    recommendation_cost_weight: float = 0.4
    recommendation_performance_weight: float = 0.3
    recommendation_reliability_weight: float = 0.3

    # Sustainability
    entsoe_token: str | None = None
    eia_api_key: str | None = None
    sustainability_boost_max: float = 0.15
    sustainability_cache_ttl_seconds: int = 900
    sustainability_cache_max_entries: int = 500

    # ── Provider credentials (ingestion) ──────────────────────────────────
    # Hetzner Cloud — https://docs.hetzner.cloud/#authentication
    hetzner_api_token: str | None = None

    # DigitalOcean — https://cloud.digitalocean.com/account/api/tokens
    digitalocean_api_token: str | None = None

    # UpCloud — https://hub.upcloud.com/account/api-tokens
    upcloud_api_token: str | None = None
    upcloud_username: str | None = None
    upcloud_password: str | None = None

    # Seeweb — https://docs.seeweb.it
    seeweb_api_token: str | None = None
    seeweb_username: str | None = None
    seeweb_password: str | None = None

    # AWS — IAM credentials (only needed for spot pricing; on-demand uses public API)
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_default_region: str | None = None

    # Azure — optional; enhances specs via Resource SKUs API (pricing is public)
    azure_subscription_id: str | None = None
    azure_access_token: str | None = None

    # Scaleway — optional; public catalog is used by default
    scaleway_access_key: str | None = None
    scaleway_secret_key: str | None = None

    # OVH — optional; public catalog is used by default
    ovh_application_key: str | None = None
    ovh_application_secret: str | None = None
    ovh_consumer_key: str | None = None
    ovh_project_id: str | None = None

    # Exoscale — https://portal.exoscale.com (config-based pricing, keys kept for future API use)
    exoscale_api_key: str | None = None
    exoscale_api_secret: str | None = None

    # Aruba Cloud — config-based pricing, credentials kept for future API use
    aruba_username: str | None = None
    aruba_password: str | None = None
    aruba_dc_id: int = 1

    # GCP — service account or ADC
    google_cloud_project: str | None = None
    google_application_credentials: str | None = None

    # IBM Cloud — https://cloud.ibm.com/iam/apikeys
    ibmcloud_api_key: str | None = None


settings = Settings()
