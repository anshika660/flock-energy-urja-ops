from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_meter_service
from app.client import UrjaNotFoundError
from app.schemas import (
    GeoLocation,
    MeterDetail,
    MeterEnergyResponse,
    MeterNearbyItem,
    MeterSummary,
    PaginatedResponse,
)
from app.service import MeterService

router = APIRouter(prefix="/api/v1/meters", tags=["Meters"])


@router.get(
    "",
    response_model=PaginatedResponse[MeterSummary],
    summary="List and search smart meters",
    description=(
        "Query meters across multiple attributes. Supports partial text search on meter ID or serial, "
        "filtering by manufacturer, phase type, installation status, transformer code, feeder code, etc."
    ),
)
async def list_meters(
    q: Optional[str] = Query(None, description="Substring search on meterId or serialNo"),
    make: Optional[str] = Query(None, description="Filter by meter manufacturer (e.g. HPL, L&T, Genus, Secure, Allied)"),
    phase_type: Optional[str] = Query(None, description="Filter by phase: 'single' or 'three'"),
    install_status: Optional[str] = Query(None, description="Filter by status: 'Installed', 'Faulty', 'Decommissioned'"),
    install_type: Optional[str] = Query(None, description="Filter by install type: 'Whole Current' or 'CT Operated'"),
    build: Optional[str] = Query(None, description="Filter by build hardware: 'legacy' or 'v2'"),
    dt_code: Optional[str] = Query(None, description="Filter by Distribution Transformer code (e.g. DT-001)"),
    feeder_code: Optional[str] = Query(None, description="Filter by Feeder code (e.g. F-001)"),
    substation_code: Optional[str] = Query(None, description="Filter by Substation code (e.g. SS-01)"),
    sort_by: str = Query("meter_id", description="Field to sort by: meter_id, serial_no, make, dt_code, install_status"),
    order: str = Query("asc", description="Sort order: 'asc' or 'desc'"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=200, description="Items per page"),
    service: MeterService = Depends(get_meter_service),
) -> PaginatedResponse[MeterSummary]:
    return await service.list_meters(
        q=q,
        make=make,
        phase_type=phase_type,
        install_status=install_status,
        install_type=install_type,
        build=build,
        dt_code=dt_code,
        feeder_code=feeder_code,
        substation_code=substation_code,
        sort_by=sort_by,
        order=order,
        page=page,
        limit=limit,
    )


@router.get(
    "/nearby",
    response_model=List[MeterNearbyItem],
    summary="Geospatial proximity meter search",
    description=(
        "Find meters within a given radius in kilometers from a geographic coordinate (latitude, longitude). "
        "Returns meters ordered from nearest to furthest with computed distance."
    ),
)
async def get_meters_nearby(
    lat: float = Query(..., description="Query latitude (WGS84, e.g. 26.9124)"),
    lng: float = Query(..., description="Query longitude (WGS84, e.g. 75.7873)"),
    radius_km: float = Query(5.0, ge=0.1, le=100.0, description="Search radius in kilometers"),
    limit: int = Query(50, ge=1, le=200, description="Max meters to return"),
    service: MeterService = Depends(get_meter_service),
) -> List[MeterNearbyItem]:
    return await service.get_meters_nearby(latitude=lat, longitude=lng, radius_km=radius_km, limit=limit)


@router.get(
    "/{meter_id}",
    response_model=MeterDetail,
    summary="Get full meter detail",
    description="Retrieve clean, unified meter details including nameplate specifications, network hierarchy, and coordinates.",
)
async def get_meter(
    meter_id: str,
    service: MeterService = Depends(get_meter_service),
) -> MeterDetail:
    try:
        return await service.get_meter_by_id(meter_id)
    except UrjaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meter '{meter_id}' not found.",
        )


@router.get(
    "/{meter_id}/geo",
    response_model=GeoLocation,
    summary="Get meter geographic coordinates",
    description="Retrieve clean latitude and longitude coordinates for a meter.",
)
async def get_meter_geo(
    meter_id: str,
    service: MeterService = Depends(get_meter_service),
) -> GeoLocation:
    try:
        meter = await service.get_meter_by_id(meter_id)
        if not meter.geo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Geographic coordinates unavailable for meter '{meter_id}'.",
            )
        return meter.geo
    except UrjaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meter '{meter_id}' not found.",
        )


@router.get(
    "/{meter_id}/energy",
    response_model=MeterEnergyResponse,
    summary="Get meter energy consumption readings",
    description=(
        "Retrieve chronological 30-minute interval readings for a meter (kWh, kVAh, voltage). "
        "Includes computed window summary analytics: net consumption, power factor, voltage min/avg/max."
    ),
)
async def get_meter_energy(
    meter_id: str,
    include_summary: bool = Query(True, description="Whether to include aggregated summary metrics"),
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Maximum readings to return"),
    service: MeterService = Depends(get_meter_service),
) -> MeterEnergyResponse:
    try:
        return await service.get_meter_energy(meter_id=meter_id, include_summary=include_summary, limit=limit)
    except UrjaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meter '{meter_id}' not found.",
        )
