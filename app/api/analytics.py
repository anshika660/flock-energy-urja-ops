from fastapi import APIRouter, Depends

from app.api.deps import get_meter_service
from app.schemas import FleetStats
from app.service import MeterService

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


@router.get(
    "/stats",
    response_model=FleetStats,
    summary="Get fleet operational statistics",
    description="Retrieve high-level fleet metrics: status breakdown, make distribution, phase counts, and total transformer capacity.",
)
async def get_fleet_stats(
    service: MeterService = Depends(get_meter_service),
) -> FleetStats:
    return await service.get_fleet_stats()

