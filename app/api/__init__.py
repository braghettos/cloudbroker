"""API routes."""

from fastapi import APIRouter

from app.api.instance_types import router as instance_types_router
from app.api.metrics import router as metrics_router
from app.api.price_analytics import router as price_analytics_router
from app.api.prices import router as prices_router
from app.api.providers import router as providers_router
from app.api.recommendations import router as recommendations_router
from app.api.regions import router as regions_router

api_router = APIRouter()
api_router.include_router(providers_router)
api_router.include_router(regions_router)
api_router.include_router(instance_types_router)
api_router.include_router(prices_router)
api_router.include_router(recommendations_router)
api_router.include_router(price_analytics_router)
api_router.include_router(metrics_router)
