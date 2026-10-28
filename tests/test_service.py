import pytest
from app.service import haversine_distance_km, parse_urja_timestamp
from app.schemas import (
    EnergyReading,
    EnergySummary,
    HierarchyLevel,
    MeterDetail,
    MeterHierarchy,
    GeoLocation,
)


def test_haversine_distance():
    # Distance between Jaipur center (26.9124, 75.7873) and Delhi center (28.6139, 77.2090) ~ 238 km
    dist = haversine_distance_km(26.9124, 75.7873, 28.6139, 77.2090)
    assert 230 < dist < 250

    # Distance to same point is 0
    assert haversine_distance_km(26.9, 75.8, 26.9, 75.8) == 0.0


def test_parse_urja_timestamp():
    raw = "23/06/2026 23:30"
    iso = parse_urja_timestamp(raw)
    assert iso == "2026-06-23T23:30:00"

    invalid = "not-a-timestamp"
    assert parse_urja_timestamp(invalid) is None


def test_hierarchy_structure_and_healing():
    h = MeterHierarchy(
        zone=HierarchyLevel(code="Z-01", name="Jaipur Zone 1"),
        feeder=HierarchyLevel(code="F-001", name="Feeder 1"),
        dt=HierarchyLevel(code="DT-001", name="Malviya Nagar DT 1"),
    )
    assert h.zone.code == "Z-01"
    assert h.feeder.code == "F-001"
    assert h.circle is None


def test_meter_detail_model():
    detail = MeterDetail(
        meter_id="J100000",
        serial_no="SE33962",
        make="HPL",
        phase_type="single",
        install_status="Installed",
        install_type="Whole Current",
        build="legacy",
        dt_code="DT-001",
        feeder_code="F-001",
        geo=GeoLocation(latitude=26.9389, longitude=75.8309),
    )
    assert detail.meter_id == "J100000"
    assert detail.make == "HPL"
    assert detail.geo.latitude == 26.9389
