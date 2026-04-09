"""Shared YAML config loader for cost-rate connectors.

Eliminates duplicated _load_config() / get_*_config_providers() boilerplate
across egress_config, storage_config, public_ip_config, and os_license_config.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CONNECTORS_DIR = Path(__file__).resolve().parent


def load_yaml_config(filename: str) -> dict:
    """Load a YAML config file from the connectors directory. Returns empty dict if missing."""
    config_path = _CONNECTORS_DIR / filename
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}
