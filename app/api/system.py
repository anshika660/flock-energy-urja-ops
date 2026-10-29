from fastapi import APIRouter, Depends

from app.api.deps import get_meter_service
from app.schemas import HealthCheck
from app.service import MeterService

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    response_model=HealthCheck,
    summary="Service health check",
    description="Check API status, upstream portal connectivity, and cache state.",
)
async def health_check(
    service: MeterService = Depends(get_meter_service),
) -> HealthCheck:
    return HealthCheck(
        status="healthy",
        portal_connected=service.client._is_authenticated or len(service._meters_cache) > 0,
        cached_meters=len(service._meters_cache),
        cache_last_updated=service.last_synced_iso,
        version="1.0.0",
    )


@router.post(
    "/api/v1/cache/refresh",
    response_model=HealthCheck,
    summary="Force refresh data cache from Urja portal",
    description="Forces a complete resynchronization of the meter fleet and transformer inventory from the upstream portal.",
)
async def refresh_cache(
    service: MeterService = Depends(get_meter_service),
) -> HealthCheck:
    await service.ensure_synced(force=True)
    return HealthCheck(
        status="refreshed",
        portal_connected=True,
        cached_meters=len(service._meters_cache),
        cache_last_updated=service.last_synced_iso,
        version="1.0.0",
    )

