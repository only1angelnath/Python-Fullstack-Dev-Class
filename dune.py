import os
import time
import logging
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from dune_client.client import DuneClient

load_dotenv()  # Load environment variables from .env file

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DUNE_QUERY_ID: int = 6598254
CACHE_TTL_SECONDS: int = 3 * 24 * 60 * 60  # 3 days
CACHE_KEY: str = f"dune_query_{DUNE_QUERY_ID}"

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[str, Any] = {
    "data": None,
    "fetched_at": 0.0,
}

# ── Dune Client and Cache Helpers ───────────────────────────────────────────────
def get_dune_client() -> DuneClient:
    api_key = os.getenv("DUNE_QUERY_API_KEY")
    if not api_key:
        raise RuntimeError("DUNE_QUERY_API_KEY environment variable is not set.")
    return DuneClient(api_key)


def is_cache_valid() -> bool:
    
    return (
        _cache["data"] is not None
        and (time.time() - _cache["fetched_at"]) < CACHE_TTL_SECONDS
    )


def fetch_from_dune() -> Any:
    logger.info("Cache miss – fetching query %s from Dune …", DUNE_QUERY_ID)
    dune = get_dune_client()
    result = dune.get_latest_result(DUNE_QUERY_ID)

    # Serialize to a plain dict so it is JSON-safe
    rows = result.result.rows if result.result else []
    metadata = {
        "execution_id": result.execution_id,
        "query_id": result.query_id,
        "state": str(result.state),
        "submitted_at": str(result.times.submitted_at) if result.times else None,
        "execution_started_at": str(result.times.execution_started_at) if result.times else None,
        "execution_ended_at": str(result.times.execution_ended_at) if result.times else None,
    }

    _cache["data"] = {"metadata": metadata, "rows": rows}
    _cache["fetched_at"] = time.time()
    logger.info("Fetched %d rows from Dune.", len(rows))
    return _cache["data"]


# ── Lifespan (warm cache on startup) ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        fetch_from_dune()
    except Exception as exc:
        logger.warning("Startup pre-fetch failed: %s", exc)
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="PRXVT Pool Activity Analytics API",
    description="FastAPI wrapper for RXVT Pool Activity Analytics using Dune query results with a 3-day TTL cache.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {"message": "Welcome to the PRXVT Pool Activity Analytics API! Visit /docs for API documentation.",
            "swagger ui": "/docs",
            "endpoints": ["/data", "/data/refresh", "/cache/status"],
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "dune_query_id": DUNE_QUERY_ID}


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", summary="Health check")
def root():
    return {"status": "ok", "service": "PRXVT Pool Activity Analytics API"}


@app.get("/data", summary="Get latest Dune query result")
def get_data():
    """
    Returns the latest result for Dune query 6598254.
    Results are cached for 3 days; the cache is refreshed automatically
    on the first request after expiry.
    """
    try:
        if is_cache_valid():
            logger.info("Cache hit – returning cached data.")
            payload = _cache["data"]
        else:
            payload = fetch_from_dune()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error while fetching Dune data.")
        raise HTTPException(status_code=502, detail=f"Dune fetch failed: {exc}")

    age_seconds = int(time.time() - _cache["fetched_at"])
    expires_in = max(0, CACHE_TTL_SECONDS - age_seconds)

    return JSONResponse(
        content={
            "cached": True if age_seconds > 0 else False,
            "cache_age_seconds": age_seconds,
            "cache_expires_in_seconds": expires_in,
            "query_id": DUNE_QUERY_ID,
            "result": payload,
        }
    )


@app.post("/data/refresh", summary="Force-refresh the cache")
def refresh_data():
    """
    Bypasses the TTL and immediately re-fetches data from Dune.
    Useful after a new query execution has been triggered manually.
    """
    try:
        payload = fetch_from_dune()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during forced refresh.")
        raise HTTPException(status_code=502, detail=f"Dune fetch failed: {exc}")

    return {"message": "Cache refreshed successfully.", "row_count": len(payload["rows"])}


@app.get("/cache/status", summary="Inspect cache state")
def cache_status():
    age = int(time.time() - _cache["fetched_at"]) if _cache["fetched_at"] else None
    return {
        "cached": _cache["data"] is not None,
        "cache_age_seconds": age,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "cache_expires_in_seconds": max(0, CACHE_TTL_SECONDS - age) if age is not None else None,
    }