from __future__ import annotations

from typing import Any, Dict, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class GeoLocation(BaseModel):
    latitude: float = Field(..., description="Latitude coordinate (WGS84)")
    longitude: float = Field(..., description="Longitude coordinate (WGS84)")


class HierarchyLevel(BaseModel):
    code: str = Field(..., description="Entity code (e.g. Z-01, F-001, DT-001)")
    name: str = Field(..., description="Human-readable name")


class MeterHierarchy(BaseModel):
    zone: Optional[HierarchyLevel] = None
    circle: Optional[HierarchyLevel] = None
    division: Optional[HierarchyLevel] = None
    subdivision: Optional[HierarchyLevel] = None
    substation: Optional[HierarchyLevel] = None
    feeder: Optional[HierarchyLevel] = None
    dt: Optional[HierarchyLevel] = None


class MeterSummary(BaseModel):
    meter_id: str = Field(..., description="Unique alphanumeric identifier (e.g. J100000)")
    serial_no: str = Field(..., description="Manufacturer serial number")
    make: str = Field(..., description="Manufacturer (e.g. HPL, L&T, Genus, Secure, Allied)")
    phase_type: str = Field(..., description="single or three phase")
    install_status: str = Field(..., description="Installed, Faulty, or Decommissioned")
    install_type: Optional[str] = Field(None, description="Whole Current or CT Operated")
    build: Optional[str] = Field(None, description="Legacy or v2 hardware build")
    dt_code: Optional[str] = Field(None, description="Distribution Transformer code")
    feeder_code: Optional[str] = Field(None, description="Connected feeder code")
    geo: Optional[GeoLocation] = Field(None, description="Geographic coordinates")


class MeterDetail(MeterSummary):
    hierarchy: MeterHierarchy = Field(default_factory=MeterHierarchy, description="Full electrical network hierarchy")
    extra_parameters: Dict[str, Any] = Field(default_factory=dict, description="Raw nameplate and diagnostic parameters")


class MeterNearbyItem(MeterSummary):
    distance_km: float = Field(..., description="Distance in kilometers from query coordinates")


class EnergyReading(BaseModel):
    timestamp: str = Field(..., description="Raw timestamp string (DD/MM/YYYY HH:MM)")
    timestamp_iso: Optional[str] = Field(None, description="Standardized ISO-8601 timestamp (YYYY-MM-DDTHH:MM:SS)")
    kwh: float = Field(..., description="Active cumulative energy in kilowatt-hours (kWh)")
    kvah: float = Field(..., description="Apparent cumulative energy in kilovolt-ampere-hours (kVAh)")
    volt_r: float = Field(..., description="R-phase voltage in Volts (V)")


class EnergySummary(BaseModel):
    reading_count: int = Field(..., description="Total number of interval readings in window")
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    start_kwh: Optional[float] = Field(None, description="Initial kWh reading")
    end_kwh: Optional[float] = Field(None, description="Final kWh reading")
    net_kwh_consumed: Optional[float] = Field(None, description="Total active energy consumed over window")
    avg_voltage_r: Optional[float] = Field(None, description="Average R-phase voltage")
    min_voltage_r: Optional[float] = Field(None, description="Minimum R-phase voltage recorded")
    max_voltage_r: Optional[float] = Field(None, description="Maximum R-phase voltage recorded")
    estimated_power_factor: Optional[float] = Field(None, description="Estimated average power factor (kWh/kVAh)")


class MeterEnergyResponse(BaseModel):
    meter_id: str = Field(..., description="Meter ID")
    summary: Optional[EnergySummary] = Field(None, description="Summary analytics for the interval window")
    readings: List[EnergyReading] = Field(default_factory=list, description="Chronological 30-minute interval readings")


class DistributionTransformer(BaseModel):
    code: str = Field(..., description="DT code (e.g. DT-001)")
    name: str = Field(..., description="DT name")
    feeder_code: str = Field(..., description="Parent feeder code")
    capacity_kva: float = Field(..., description="Rated capacity in kVA")
    meter_count: int = Field(0, description="Number of connected meters")


class DTDetail(DistributionTransformer):
    meters: List[MeterSummary] = Field(default_factory=list, description="List of connected meters")


class HierarchyTreeNode(BaseModel):
    level: str = Field(..., description="Hierarchy depth: zone, circle, division, subdivision, substation, feeder, dt")
    code: str = Field(..., description="Unique entity code at this level")
    name: str = Field(..., description="Display name")
    meter_count: int = Field(0, description="Total meters subordinate to this node")
    children: List[HierarchyTreeNode] = Field(default_factory=list, description="Subordinate nodes")


class HierarchyLevelSummaryItem(BaseModel):
    code: str
    name: str
    meter_count: int


class HierarchySummary(BaseModel):
    zones: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    circles: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    divisions: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    subdivisions: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    substations: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    feeders: List[HierarchyLevelSummaryItem] = Field(default_factory=list)
    dts: List[HierarchyLevelSummaryItem] = Field(default_factory=list)


class FleetStats(BaseModel):
    total_meters: int
    by_status: Dict[str, int]
    by_make: Dict[str, int]
    by_phase: Dict[str, int]
    by_build: Dict[str, int]
    total_dts: int
    total_feeders: int
    total_substations: int
    total_transformer_capacity_kva: float


class PaginatedResponse(BaseModel, Generic[T]):
    data: List[T]
    total: int
    page: int
    limit: int
    total_pages: int


class HealthCheck(BaseModel):
    status: str
    portal_connected: bool
    cached_meters: int
    cache_last_updated: Optional[str]
    version: str = "1.0.0"

