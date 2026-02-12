"""Aruba Cloud connector: fetch flavors and pricing, ingest into DB."""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fx import get_rate_to_eur
from app.core.logging import get_logger
from app.models.price import PurchaseModel
from app.models.provider import ProviderType
from app.services.ingestion import (
    ensure_instance_type,
    ensure_provider,
    ensure_region,
    append_price,
)

logger = get_logger(__name__)

ARUBA_BASE = "https://api.arubacloud.com"
ARUBA_AUTH_URL = "https://mylogin.aruba.it/auth/realms/cmp-new-apikey/protocol/openid-connect/token"

# Aruba Cloud EU regions
# Based on Aruba Cloud data centers
ARUBA_EU_REGIONS = [
    {"slug": "ITBG", "name": "Bergamo", "country": "IT", "is_eu": True},
    # Aruba Cloud may have more regions, but ITBG (Bergamo) is confirmed
]


async def _get_access_token(api_key: str, api_secret: str) -> str:
    """Get OAuth2 access token using client credentials flow."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            ARUBA_AUTH_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": api_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        data = response.json()
        return data["access_token"]


async def _fetch_projects(token: str) -> list[dict]:
    """Fetch available projects from Aruba Cloud API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try multiple possible endpoints
        endpoints = ["/projects", "/project"]

        for endpoint in endpoints:
            try:
                response = await client.get(
                    f"{ARUBA_BASE}{endpoint}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
                logger.debug("Projects API response", endpoint=endpoint, data_type=type(data).__name__)

                # Handle both list and object responses
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    # Check for common response structures
                    if "values" in data:
                        values = data["values"]
                        if isinstance(values, list):
                            return values
                    if "items" in data:
                        items = data["items"]
                        if isinstance(items, list):
                            return items
                    if "projects" in data:
                        projects = data["projects"]
                        if isinstance(projects, list):
                            return projects
                    if "value" in data:
                        value = data["value"]
                        if isinstance(value, list):
                            return value
                    if "id" in data:
                        # Single project object
                        return [data]
                    # Log structure for debugging
                    logger.debug("Projects response structure", keys=list(data.keys())[:15], sample=str(data)[:200])
                    # If it's a dict but we can't parse it, return empty
                    return []
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue  # Try next endpoint
                logger.warning("Error fetching projects", endpoint=endpoint, status=e.response.status_code)
                raise

        logger.warning("No projects endpoint worked, returning empty list")
        return []


async def _fetch_flavors(token: str, project_id: str) -> list[dict]:
    """Fetch available flavors (instance types) from Aruba Cloud API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try multiple possible endpoints for flavors
        # Aruba Cloud API might use different provider names or paths
        endpoints = [
            f"/projects/{project_id}/providers/Aruba.Compute/flavors?api-version=1.0",
            f"/projects/{project_id}/providers/Aruba.Compute/flavors",
            f"/projects/{project_id}/providers/Aruba.CmpService.Computing/flavors",
            f"/projects/{project_id}/flavors",
            # Try listing cloud servers to extract flavors from them
            f"/projects/{project_id}/providers/Aruba.Compute/cloudServers?api-version=1.0",
        ]

        for endpoint in endpoints:
            try:
                response = await client.get(
                    f"{ARUBA_BASE}{endpoint}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()

                # If this is a cloudServers endpoint, extract unique flavors
                if "cloudServers" in endpoint:
                    # Extract flavors from cloud server responses
                    servers = data.get("values") or data.get("items") or (data if isinstance(data, list) else [])
                    logger.debug("Cloud servers response", server_count=len(servers) if isinstance(servers, list) else 0)
                    flavors_seen = {}
                    for server in servers:
                        # Flavor can be in properties.flavor or directly in flavor
                        server_props = server.get("properties", {}) if isinstance(server, dict) else {}
                        flavor = server_props.get("flavor") or server.get("flavor")
                        if flavor and isinstance(flavor, dict):
                            flavor_id = flavor.get("id") or flavor.get("name")
                            if flavor_id and flavor_id not in flavors_seen:
                                flavors_seen[flavor_id] = flavor
                    if flavors_seen:
                        logger.info("Extracted flavors from cloud servers", count=len(flavors_seen))
                        return list(flavors_seen.values())
                    else:
                        logger.warning("No flavors found in cloud servers - account may have no servers")
                        # Return empty - we can't get flavors without existing servers
                        return []

                # Handle both list and object responses
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    if "values" in data:
                        values = data["values"]
                        if isinstance(values, list):
                            return values
                    if "items" in data:
                        items = data["items"]
                        if isinstance(items, list):
                            return items
                    if "flavors" in data:
                        flavors = data["flavors"]
                        if isinstance(flavors, list):
                            return flavors
                    if "id" in data or "name" in data:
                        return [data]  # Single flavor
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue  # Try next endpoint
                elif e.response.status_code == 403:
                    logger.debug("403 Forbidden for endpoint", endpoint=endpoint)
                    continue  # Try next endpoint
                logger.warning("Error fetching flavors", endpoint=endpoint, status=e.response.status_code)
                # Don't raise, try next endpoint

        # If no endpoint worked, return empty list
        logger.warning("Could not find flavors endpoint, returning empty list")
        return []


async def _fetch_pricing(token: str, project_id: str, flavor_id: str) -> dict | None:
    """Fetch pricing for a specific flavor."""
    # Aruba Cloud pricing might be in flavor details or a separate pricing endpoint
    async with httpx.AsyncClient(timeout=30.0) as client:
        endpoint = f"/projects/{project_id}/providers/Aruba.Compute/flavors/{flavor_id}"
        try:
            response = await client.get(
                f"{ARUBA_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            logger.warning("Could not fetch pricing for flavor", flavor_id=flavor_id)
            return None


async def ingest(
    db: AsyncSession,
    only_eu: bool = False,
    purchase_model: str = "on_demand",
) -> None:
    """Ingest Aruba Cloud instance types and pricing into database."""
    api_key = (
        os.environ.get("ARUBA_API_KEY")
        or os.environ.get("ARUBA_CLIENT_ID")
        or os.environ.get("ARUBACLOUD_API_KEY")
        or os.environ.get("ARUBACLOUD_CLIENT_ID")
    )
    api_secret = (
        os.environ.get("ARUBA_API_SECRET")
        or os.environ.get("ARUBA_CLIENT_SECRET")
        or os.environ.get("ARUBACLOUD_API_SECRET")
        or os.environ.get("ARUBACLOUD_CLIENT_SECRET")
    )
    project_id = os.environ.get("ARUBA_PROJECT_ID") or os.environ.get("ARUBACLOUD_PROJECT_ID")

    if not all([api_key, api_secret]):
        raise RuntimeError(
            "ARUBA_API_KEY and ARUBA_API_SECRET "
            "environment variables are required for Aruba Cloud ingestion"
        )

    # Get access token
    token = await _get_access_token(api_key, api_secret)
    logger.info("Obtained Aruba Cloud access token")

    # If project_id not provided, fetch projects and use the first one
    if not project_id:
        try:
            projects = await _fetch_projects(token)
            if not projects:
                raise RuntimeError("No projects found in Aruba Cloud account")
            # Use first project - ID can be in metadata.id or directly in id
            first_project = projects[0]
            project_id = (
                first_project.get("id")
                or first_project.get("metadata", {}).get("id")
                or (first_project.get("metadata") or {}).get("id")
            )
            if not project_id:
                logger.error("Could not extract project ID", project_structure=str(first_project)[:200])
                raise RuntimeError("Could not extract project ID from projects list")
            logger.info("Using project from API", project_id=project_id, project_name=first_project.get("metadata", {}).get("name", "unknown"))
        except Exception as e:
            raise RuntimeError(
                f"ARUBA_PROJECT_ID not set and could not fetch projects: {e}. "
                "Please set ARUBA_PROJECT_ID environment variable."
            )

    # Ensure provider
    provider = await ensure_provider(
        db,
        slug="aruba",
        name="Aruba Cloud",
        website="https://www.arubacloud.com",
        type=ProviderType.EU,
    )
    await db.flush()

    # Ensure regions
    region_by_slug = {}
    for region_info in ARUBA_EU_REGIONS:
        if only_eu and not region_info["is_eu"]:
            continue

        region = await ensure_region(
            db,
            provider_id=provider.id,
            slug=region_info["slug"],
            name=region_info["name"],
            country=region_info["country"],
            is_eu=region_info["is_eu"],
        )
        region_by_slug[region_info["slug"]] = region

    await db.flush()
    logger.info("Created/updated regions", count=len(region_by_slug))

    # Ensure FX rate for EUR exists (Aruba prices are in EUR)
    await get_rate_to_eur(db, "EUR")

    # Fetch flavors
    # Note: Aruba Cloud API doesn't expose a flavors catalog endpoint.
    # Flavors are only available from existing cloud servers.
    # If no servers exist, we can't get flavors.
    try:
        flavors = await _fetch_flavors(token, project_id)
        logger.info("Fetched flavors", count=len(flavors))

        if len(flavors) == 0:
            logger.warning(
                "No flavors found. Aruba Cloud API requires existing cloud servers to extract flavors. "
                "Please create at least one cloud server in your Aruba Cloud account, or manually set ARUBA_PROJECT_ID "
                "if you have servers in a different project."
            )
            return
    except Exception as e:
        logger.error("Failed to fetch flavors", error=str(e))
        raise

    # Process flavors
    inserted_prices = 0
    instance_types_seen = set()

    for flavor in flavors:
        flavor_id = flavor.get("id") or flavor.get("name")
        if not flavor_id:
            continue

        # Extract instance specs
        vcpu = int(flavor.get("cpu") or flavor.get("vcpu") or flavor.get("cores") or 0)
        ram_mb = flavor.get("ram") or 0
        ram_gb = float(ram_mb) / 1024.0 if ram_mb else 0.0

        # Aruba Cloud instances are typically x86_64
        arch = "x86_64"

        # Infer family from flavor category or name
        flavor_name = flavor.get("name") or str(flavor_id)
        category = flavor.get("category", "").lower()
        family = _infer_family(flavor_name, category)

        # Ensure instance type
        it = await ensure_instance_type(
            db,
            provider_id=provider.id,
            name=flavor_name,
            vcpu=vcpu,
            ram_gb=ram_gb,
            family=family,
            arch=arch,
        )
        await db.flush()
        instance_types_seen.add(flavor_name)

        # Get pricing for each region
        for region_slug, region in region_by_slug.items():
            # Try to get pricing from flavor details
            pricing = await _fetch_pricing(token, project_id, flavor_id)

            # Extract hourly price (Aruba Cloud uses EUR)
            price_hourly_eur = None
            if pricing:
                price_hourly_eur = (
                    pricing.get("hourlyPrice")
                    or pricing.get("price")
                    or pricing.get("hourly")
                    or pricing.get("pricePerHour")
                )

            # If not in pricing details, try flavor directly
            if price_hourly_eur is None:
                price_hourly_eur = (
                    flavor.get("hourlyPrice")
                    or flavor.get("price")
                    or flavor.get("hourly")
                )

            if price_hourly_eur is None or price_hourly_eur <= 0:
                logger.warning(
                    "Skipping flavor with invalid pricing",
                    flavor=flavor_name,
                    region=region_slug,
                )
                continue

            price_hourly_eur = float(Decimal(str(price_hourly_eur)))

            # Convert purchase model
            purchase = PurchaseModel(purchase_model)

            # Append price
            p = await append_price(
                db,
                instance_type_id=it.id,
                region_id=region.id,
                purchase_model=purchase,
                currency="EUR",
                price_per_hour=price_hourly_eur,
                price_eur_per_hour=price_hourly_eur,
            )
            if p:
                inserted_prices += 1

    logger.info(
        "Aruba Cloud ingestion completed",
        instance_types=len(instance_types_seen),
        price_rows_inserted=inserted_prices,
        provider_slug="aruba",
        regions=len(region_by_slug),
    )


def _infer_family(flavor_name: str, category: str) -> str:
    """Infer instance family from Aruba Cloud flavor name and category."""
    name_lower = flavor_name.lower()
    category_lower = category.lower()

    if "gpu" in name_lower or "gpu" in category_lower:
        return "gpu"
    elif "compute" in category_lower or "cpu" in category_lower:
        return "compute-optimized"
    elif "memory" in category_lower or "ram" in category_lower:
        return "memory-optimized"
    elif "storage" in category_lower or "io" in category_lower:
        return "io-optimized"
    else:
        return "general-purpose"
