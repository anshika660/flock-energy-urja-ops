from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_meter_service
from app.client import UrjaNotFoundError
from app.schemas import (
    DistributionTransformer,
    DTDetail,
    HierarchySummary,
    HierarchyTreeNode,
)
from app.service import MeterService

router = APIRouter(prefix="/api/v1/hierarchy", tags=["Network Hierarchy"])


@router.get(
    "/tree",
    response_model=HierarchyTreeNode,
    summary="Get reconstructed electrical network hierarchy tree",
    description=(
        "Reconstructs the hierarchical topology: Discom -> Zone -> Circle -> Division -> Subdivision -> "
        "Substation -> Feeder -> DT -> Meters. Automatically resolves legacy naming and coding inconsistencies."
    ),
)
async def get_hierarchy_tree(
    service: MeterService = Depends(get_meter_service),
) -> HierarchyTreeNode:
    return await service.get_hierarchy_tree()


@router.get(
    "/summary",
    response_model=HierarchySummary,
    summary="Get hierarchy breakdown summary",
    description="Returns distinct entities across all levels (zones, circles, divisions, feeders, DTs) with meter counts.",
)
async def get_hierarchy_summary(
    service: MeterService = Depends(get_meter_service),
) -> HierarchySummary:
    return await service.get_hierarchy_summary()


@router.get(
    "/transformers",
    response_model=List[DistributionTransformer],
    summary="List distribution transformers (DTs)",
    description="Returns all distribution transformers with ratings, feeder associations, and connected meter counts.",
)
async def list_transformers(
    service: MeterService = Depends(get_meter_service),
) -> List[DistributionTransformer]:
    return await service.get_transformers()


@router.get(
    "/transformers/{dt_code}",
    response_model=DTDetail,
    summary="Get transformer detail and connected meters",
    description="Retrieve details of a specific transformer along with the list of smart meters connected under it.",
)
async def get_transformer(
    dt_code: str,
    service: MeterService = Depends(get_meter_service),
) -> DTDetail:
    try:
        return await service.get_transformer_detail(dt_code)
    except UrjaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transformer '{dt_code}' not found.",
        )
