"""Recommendations endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_api_key
from app.schemas.recommendations import RecommendationRequest, RecommendationResponse
from app.services.recommendation_service import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("", response_model=RecommendationResponse)
async def create_recommendations(
    body: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
) -> RecommendationResponse:
    """Return ranked instance type + region recommendations by min specs, region and max price."""
    service = RecommendationService(db)
    recommendations = await service.get_recommendations(body)
    return RecommendationResponse(recommendations=recommendations)
