from typing import Generator
from fastapi import Request
from app.service import MeterService

# Singleton service instance shared across requests
_service_instance: MeterService = MeterService()


def get_meter_service() -> MeterService:
    """Dependency provider returning the singleton MeterService instance."""
    return _service_instance

