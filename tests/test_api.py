import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "portal_connected" in data
    assert "cached_meters" in data
    assert data["version"] == "1.0.0"


def test_list_meters():
    response = client.get("/api/v1/meters?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert "total" in data
    assert len(data["data"]) <= 5
    assert data["total"] > 0

    first = data["data"][0]
    assert "meter_id" in first
    assert "serial_no" in first
    assert "make" in first
    assert "phase_type" in first
    assert "install_status" in first


def test_list_meters_filtering():
    response = client.get("/api/v1/meters?make=HPL&install_status=Installed")
    assert response.status_code == 200
    data = response.json()
    for m in data["data"]:
        assert m["make"] == "HPL"
        assert m["install_status"] == "Installed"


def test_get_meter_by_id():
    response = client.get("/api/v1/meters/J100000")
    assert response.status_code == 200
    data = response.json()
    assert data["meter_id"] == "J100000"
    assert "hierarchy" in data
    assert "geo" in data


def test_get_meter_not_found():
    response = client.get("/api/v1/meters/NON_EXISTENT_9999")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


def test_get_meter_geo():
    response = client.get("/api/v1/meters/J100000/geo")
    assert response.status_code == 200
    data = response.json()
    assert "latitude" in data
    assert "longitude" in data
    assert isinstance(data["latitude"], float)
    assert isinstance(data["longitude"], float)


def test_get_meter_energy():
    response = client.get("/api/v1/meters/J100000/energy?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["meter_id"] == "J100000"
    assert "readings" in data
    assert len(data["readings"]) == 10
    assert "summary" in data
    assert data["summary"]["reading_count"] > 0
    assert "net_kwh_consumed" in data["summary"]


def test_meters_nearby():
    # Query Jaipur coordinates
    response = client.get("/api/v1/meters/nearby?lat=26.9124&lng=75.7873&radius_km=10&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    first = data[0]
    assert "distance_km" in first
    assert first["distance_km"] <= 10.0


def test_hierarchy_tree():
    response = client.get("/api/v1/hierarchy/tree")
    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "discom"
    assert "children" in data
    assert len(data["children"]) > 0
    assert data["meter_count"] > 0


def test_hierarchy_transformers():
    response = client.get("/api/v1/hierarchy/transformers")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    dt01 = next(d for d in data if d["code"] == "DT-001")
    assert dt01["capacity_kva"] > 0
    assert dt01["meter_count"] > 0


def test_transformer_detail():
    response = client.get("/api/v1/hierarchy/transformers/DT-001")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == "DT-001"
    assert "meters" in data
    assert len(data["meters"]) == data["meter_count"]


def test_analytics_stats():
    response = client.get("/api/v1/analytics/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_meters"] > 0
    assert "by_status" in data
    assert "by_make" in data
    assert "by_phase" in data
    assert data["total_transformer_capacity_kva"] > 0


def test_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Urja Meter Ops" in response.text
