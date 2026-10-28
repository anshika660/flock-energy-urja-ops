import contextlib
import json
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import analytics, hierarchy, meters, system
from app.api.deps import get_meter_service
from app.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("urja_ops_api")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup & shutdown lifecycle.
    Pre-warms the service cache on startup so all subsequent requests
    benefit from instant in-memory response times.
    """
    logger.info("Initializing Urja Meter Ops Gateway Service...")
    service = get_meter_service()
    try:
        await service.ensure_synced()
        logger.info(f"Service pre-warmed successfully. {len(service._meters_cache)} meters ready.")
    except Exception as e:
        logger.error(f"Initial sync warning: {e}. Will sync on-demand on first request.")

    # Export openapi.json to repository root
    try:
        openapi_data = app.openapi()
        root_openapi = Path(__file__).parent.parent / "openapi.json"
        with open(root_openapi, "w", encoding="utf-8") as f:
            json.dump(openapi_data, f, indent=2)
        logger.info(f"Exported fresh openapi.json to {root_openapi}")
    except Exception as e:
        logger.warning(f"Could not export openapi.json: {e}")

    yield

    logger.info("Shutting down Urja Meter Ops Gateway Service...")
    await service.client.close()


app = FastAPI(
    title="Urja Meter Ops Gateway API",
    version="1.0.0",
    description=(
        "Production-grade, high-performance REST API gateway in front of the legacy "
        "Urja Meter Ops smart-metering portal. Exposes normalized meter fleet records, "
        "reconstructed electrical network hierarchies, interval energy analytics, and geospatial querying."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS middleware for open integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(meters.router)
app.include_router(hierarchy.router)
app.include_router(analytics.router)
app.include_router(system.router)

# Mount static folder
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def serve_dashboard():
    """Serve the modern web client dashboard."""
    html_path = static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Urja Meter Ops Modern Gateway</h1><p>Visit <a href='/docs'>/docs</a> for API documentation.</p>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
