"""Centralized cloud API client factories.

Eliminates duplicated _get_pricing_client() and _get_billing_client()
across aws.py, egress_aws.py, storage_aws.py, egress_gcp.py, storage_gcp.py.
"""

from __future__ import annotations


def get_aws_pricing_client():
    """Return boto3 Pricing client (us-east-1). Used by egress + storage connectors."""
    try:
        import boto3
    except ImportError:
        raise RuntimeError("boto3 is required for AWS ingestion. Install with: pip install boto3")
    return boto3.client("pricing", region_name="us-east-1")


def get_gcp_billing_client():
    """Return google-cloud-billing CloudCatalogClient."""
    try:
        from google.cloud import billing_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-billing is required for GCP ingestion. Install with: pip install google-cloud-billing"
        )
    return billing_v1.CloudCatalogClient()
