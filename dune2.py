import os
import json
import time
import asyncio
from dataclasses import dataclass
from dotenv import load_dotenv
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import logging
import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from dune_client.client import DuneClient

# ---------------------------------------------------------------------------
# ENVIRONMENT VARIABLES
# ---------------------------------------------------------------------------
# load_dotenv() reads your .env file and loads all the key=value pairs into
# the environment so we can access them with os.getenv() later.
#
# Without this line, os.getenv() would return None for anything in .env
# because .env files are NOT automatically loaded by Python.
load_dotenv()

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
# Python's built-in logging module lets us print structured messages to the
# terminal with timestamps and severity levels (INFO, WARNING, ERROR, etc).
#
# basicConfig sets the minimum level to INFO, meaning INFO and above
# (WARNING, ERROR, CRITICAL) will be shown. DEBUG messages will be hidden.
#
# We then create a logger specifically named after this module (__name__).
# This means log messages will show "main" as the source, which is useful
# when you have multiple files and want to know where a message came from.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QUERY REGISTRY
# ---------------------------------------------------------------------------
# This is the heart of the API's design. Instead of hardcoding a single
# Dune query ID, we use a "registry" — a dictionary that maps a human-
# readable name to a configuration object for each query.
#
# The benefit: to add a new Dune query to this API, you only need to add
# one entry here. Every other part of the code (caching, routes, background
# refresh) works automatically with whatever is in this registry.

# @dataclass is a Python decorator that automatically generates __init__,
# __repr__, and other boilerplate methods for a class. It lets us define
# a simple data container without writing a full class manually.
#
# QueryConfig holds three pieces of information about each Dune query:
#   - query_id:    the numeric ID from the Dune Analytics URL
#   - ttl:         "time to live" — how long (in seconds) to keep cached data
#                  before fetching fresh data. Default is 3 days (259200s).
#   - description: a human-readable description, used in API responses
@dataclass
class QueryConfig:
    query_id: int
    ttl: int = 60 * 60 * 24 * 3  # 60s * 60m * 24h * 3d = 259200 seconds = 3 days
    description: str = ""

# QUERY_REGISTRY is a plain Python dictionary.
# Key   → the name you'll use in the API URL, e.g. GET /data/stablecoins
# Value → a QueryConfig object with that query's settings
#
# To add a new query, just add a new entry:
#   "my_new_query": QueryConfig(
#       query_id=1234567,
#       ttl=60 * 60 * 6,   # override to 6 hours if you want fresher data
#       description="What this query does",
#   ),
QUERY_REGISTRY: dict[str, QueryConfig] = {
    "stablecoins": QueryConfig(
        query_id=5681885,
        description="Weekly Mint, Burn, and Circulating Supply of Major Stablecoins on Ethereum",
    ),
    "traders": QueryConfig(
        query_id=5972407,
        description="Random traders on Ethereum (their activity, value and consistency in the last 365 days)",
    ),
    "prxvtstakers": QueryConfig(
        query_id=6556289,
        description="Top stakers in the PRXVT protocol",
    )
    # "ronin": QueryConfig(
    #     query_id=5785149,
    #     description="Katana Ronin Volume & User Segmentation",
    # )
}


# ---------------------------------------------------------------------------
# CACHE KEY HELPER
# ---------------------------------------------------------------------------
# Every piece of cached data needs a unique identifier (a "key") so we can
# store and retrieve it. This function takes a query name like "stablecoins"
# and returns a namespaced key like "dune:query:stablecoins".
#
# Namespacing (the "dune:query:" prefix) is good practice — it avoids
# collisions if you ever store other things in the same Redis instance.
# For example, you might later store "user:session:abc123" in the same Redis,
# and the prefixes keep everything organised and separate.
def _cache_key(name: str) -> str:
    return f"dune:query:{name}"


# ---------------------------------------------------------------------------
# DUNE CLIENT SETUP
# ---------------------------------------------------------------------------
# We read the Dune API key from environment variables (set in your .env file).
# os.getenv() returns None if the variable doesn't exist.
#
# If the key is missing, we raise a RuntimeError immediately on startup.
# This is called "fail fast" — it's better to crash with a clear error
# message at startup than to run silently and fail later when a request
# comes in. The person running the server will immediately know what's wrong.
dune_api_key = os.getenv("DUNE_QUERY_API_KEY")
if not dune_api_key:
    raise RuntimeError("DUNE_QUERY_API_KEY environment variable is not set")

# DuneClient is the official Python SDK for Dune Analytics.
# We create one instance here at module level and reuse it everywhere.
# Creating it once is more efficient than creating a new connection
# every time we want to fetch data.
dune = DuneClient(dune_api_key)


# ---------------------------------------------------------------------------
# CACHE SETUP: REDIS WITH IN-MEMORY FALLBACK
# ---------------------------------------------------------------------------
# Caching means storing data temporarily so we don't have to fetch it again
# on every request. Dune queries can take 5-15 seconds — without a cache,
# every single API request would make your users wait that long.
#
# We support two cache backends:
#
#   1. Redis — a fast, external key-value store that persists across server
#      restarts and can be shared between multiple server instances.
#      Ideal for production.
#
#   2. In-memory (a plain Python dict) — lives inside the running process.
#      Fast, zero setup, but dies when the server restarts and can't be
#      shared between multiple instances. Fine for development or simple deploys.
#
# The code tries Redis first. If Redis isn't available (wrong URL, not running,
# etc), it silently falls back to in-memory. This makes the API work in both
# local development (no Redis needed) and production (Redis available).

# These two dicts power the in-memory cache:
#   _memory_cache stores the actual data
#   _cache_meta   stores metadata (when it was cached, what the TTL is)
#                 — we need this separately because Redis handles TTL for us,
#                 but in-memory we have to track it ourselves
_memory_cache: dict = {}
_cache_meta: dict = {}

# We wrap the Redis connection attempt in a try/except block.
# If ANYTHING goes wrong (Redis not running, wrong URL, network issue),
# the except block catches it and we fall back to in-memory gracefully.
try:
    # os.getenv("REDIS_URL", "redis://localhost:6379") means:
    #   "read REDIS_URL from environment, but if it's not set,
    #    use redis://localhost:6379 as the default"
    # decode_responses=True means Redis will return Python strings
    # instead of raw bytes — much easier to work with.
    _redis_client = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True
    )
    # ping() sends a test command to Redis. If Redis isn't reachable,
    # this raises an exception immediately, which our except catches.
    # This is called "fail fast" — we know immediately if Redis is down
    # rather than finding out on the first real request.
    _redis_client.ping()
    USE_REDIS = True
    print("Cache: Redis connected")
except Exception:
    _redis_client = None
    USE_REDIS = False
    print("Cache: Redis unavailable, falling back to in-memory")


# ---------------------------------------------------------------------------
# CACHE OPERATIONS
# ---------------------------------------------------------------------------
# These three functions (get, set, delete) are the only way the rest of the
# code interacts with the cache. They check USE_REDIS and route to either
# Redis or the in-memory dict automatically.
#
# This pattern is called "abstraction" — the rest of the code doesn't need
# to know or care which backend is being used. It just calls cache_get(),
# cache_set(), cache_delete() and gets the right behaviour either way.

def cache_get(key: str):
    """
    Retrieve a value from the cache.
    Returns the cached data if it exists and hasn't expired.
    Returns None if the key doesn't exist or the TTL has passed.
    """
    if USE_REDIS:
        # Redis GET returns the stored string, or None if the key doesn't exist.
        # Redis handles TTL expiry automatically — once a key expires, GET
        # returns None as if it was never there.
        val = _redis_client.get(key)
        # We stored the data as a JSON string (see cache_set), so we need to
        # parse it back into a Python object with json.loads().
        return json.loads(val) if val else None

    # In-memory path: check if the key exists in our dict
    entry = _memory_cache.get(key)
    if entry:
        age_seconds = time.time() - entry["ts"]
        if age_seconds < entry["ttl"]:
            # Data exists and hasn't expired — return it
            return entry["data"]
    # Key doesn't exist or TTL has passed
    return None


def cache_set(key: str, value, ttl: int):
    """
    Store a value in the cache with an expiry time (TTL in seconds).
    Also records metadata so we can report cache age in API responses.
    """
    if USE_REDIS:
        # setex = SET with EXpiry. Arguments: key, ttl_in_seconds, value.
        # Redis will automatically delete this key after ttl seconds.
        # json.dumps() converts the Python list/dict to a JSON string
        # because Redis only stores strings, not Python objects.
        _redis_client.setex(key, ttl, json.dumps(value))
    else:
        # In-memory: store the data alongside its timestamp and TTL
        # so cache_get() can check if it's expired.
        # time.time() returns the current Unix timestamp (seconds since 1970).
        _memory_cache[key] = {"data": value, "ts": time.time(), "ttl": ttl}

    # Always update _cache_meta regardless of backend.
    # This powers the cache age reporting in API responses (cache_age_hours,
    # last_updated, next_refresh). Redis doesn't expose when a key was set,
    # so we track it ourselves in both cases.
    _cache_meta[key] = {"cached_at": time.time(), "ttl": ttl}


def cache_delete(key: str):
    """
    Remove a key from the cache entirely.
    Used by the DELETE /cache/{name} endpoint to force a fresh fetch.
    """
    if USE_REDIS:
        _redis_client.delete(key)
    else:
        # dict.pop(key, None) removes the key if it exists.
        # The None means "don't raise an error if the key isn't there".
        _memory_cache.pop(key, None)
    # Always clear metadata too so cache age reporting resets cleanly
    _cache_meta.pop(key, None)


def get_cache_info(key: str) -> dict:
    """
    Build a dict of human-readable cache metadata for a given key.
    This gets included in every /data/{name} response so the caller
    knows how fresh the data is and when it will next be refreshed.
    """
    meta = _cache_meta.get(key)

    # If we have no metadata, the key has never been cached
    if not meta:
        return {
            "is_cached": False,
            "is_fresh": False,
            "cache_age_hours": None,
            "last_updated": None,
            "next_refresh": None,
        }

    age_seconds = time.time() - meta["cached_at"]
    age_hours = round(age_seconds / 3600, 2)  # convert seconds → hours
    ttl = meta["ttl"]
    is_fresh = age_seconds < ttl  # True if TTL hasn't expired yet

    # datetime.fromtimestamp() converts a Unix timestamp to a human-readable
    # datetime object. .isoformat() formats it as "2025-04-28T14:30:00".
    last_updated = datetime.fromtimestamp(meta["cached_at"]).isoformat()

    # next_refresh = when the data was cached + how long it lives
    next_refresh = (
        datetime.fromtimestamp(meta["cached_at"]) + timedelta(seconds=ttl)
    ).isoformat()

    return {
        "is_cached": True,
        "is_fresh": is_fresh,
        "cache_age_hours": age_hours,
        "last_updated": last_updated,
        "next_refresh": next_refresh,
    }


# ---------------------------------------------------------------------------
# CORE DATA FETCHING
# ---------------------------------------------------------------------------

async def fetch_from_dune(query_id: int) -> list[dict]:
    """
    Fetch the latest result rows for a Dune query by its numeric ID.

    The Dune SDK's get_latest_result() is a BLOCKING call — it makes an HTTP
    request and waits for the response. In an async application like FastAPI,
    blocking calls are a problem because they freeze the entire event loop
    while waiting, meaning no other requests can be handled during that time.

    To avoid this, we use run_in_executor() which runs the blocking function
    in a separate thread. The event loop can keep handling other requests
    while the thread waits for Dune to respond.

    Think of it like this: instead of you (the event loop) standing in a queue
    at the bank, you hire someone else (a thread) to stand in the queue for
    you while you go do other things. When they're done, they hand you the result.
    """
    loop = asyncio.get_event_loop()

    # We define the blocking work as an inner function so run_in_executor
    # can call it in a thread. Inner functions can access variables from
    # the outer scope (query_id, dune) — this is called a "closure".
    def _fetch():
        result = dune.get_latest_result(query_id)
        return result.result.rows

    # run_in_executor(None, _fetch) means:
    #   - None → use the default thread pool (Python manages the threads)
    #   - _fetch → the function to run in that thread
    # The await means: "pause here and wait for the thread to finish,
    # but let the event loop handle other things in the meantime"
    return await loop.run_in_executor(None, _fetch)


async def get_data(name: str) -> list[dict]:
    """
    The main data access function. Implements the cache-aside pattern:
      1. Check the cache first
      2. If found (cache hit)  → return cached data immediately
      3. If not found (miss)   → fetch from Dune, cache it, then return it

    This is the function all routes call. They never call fetch_from_dune
    directly — everything goes through get_data() so caching is always applied.
    """
    config = QUERY_REGISTRY.get(name)
    if config is None:
        raise KeyError(f"Unknown query: '{name}'")

    key = _cache_key(name)

    # Step 1: try the cache
    cached = cache_get(key)
    if cached is not None:
        # Cache hit — return immediately without touching Dune
        return cached

    # Step 2: cache miss — fetch fresh data from Dune
    rows = await fetch_from_dune(config.query_id)

    # Step 3: store in cache so the next request is fast
    cache_set(key, rows, config.ttl)

    return rows


# ---------------------------------------------------------------------------
# BACKGROUND REFRESH LOOP
# ---------------------------------------------------------------------------
# The background refresh loop solves a specific problem: without it, cached
# data expires silently and the NEXT user to request expired data has to wait
# 5-15 seconds for Dune to respond (a "cold cache" hit).
#
# With the background loop, the server proactively refreshes data before
# (or just after) it expires, so every user always gets a fast cache hit.
#
# Think of it like a shop that restocks shelves at night so customers never
# see an empty shelf, rather than restocking only after a customer complains.

async def refresh_query(name: str, config: QueryConfig):
    """
    Fetch fresh data for a single query from Dune and update the cache.
    Called by the background loop — never directly by a route.
    Errors are caught and logged rather than raised, so one failed refresh
    doesn't crash the entire background loop.
    """
    try:
        logger.info(f"Background refresh: fetching '{name}' (query_id={config.query_id})")
        rows = await fetch_from_dune(config.query_id)
        key = _cache_key(name)
        cache_set(key, rows, config.ttl)
        logger.info(f"Background refresh: '{name}' done — {len(rows)} rows cached")
    except Exception as e:
        # Log the error but don't re-raise it. If we raised here, the entire
        # background_refresh_loop would crash and stop refreshing all queries.
        # By catching it, one failing query doesn't affect the others.
        logger.error(f"Background refresh failed for '{name}': {e}")


async def background_refresh_loop():
    """
    A long-running async task that keeps the cache warm automatically.

    Phase 1 — Startup warm-up:
        On server start, any query not yet in the cache is fetched immediately.
        This ensures the API has data ready before the first user request arrives.

    Phase 2 — TTL monitoring loop:
        Every 60 seconds, check every query in the registry. If a query's
        cached data has expired (age >= TTL), fetch fresh data from Dune.

    The asyncio.sleep(5) between queries is rate limiting — it prevents us
    from firing many Dune requests simultaneously, which could hit API rate
    limits or overload the Dune API.
    """
    # --- Phase 1: warm up the cache on startup ---
    for name, config in QUERY_REGISTRY.items():
        key = _cache_key(name)
        if cache_get(key) is None:
            # This query has no cached data yet — fetch it now
            await refresh_query(name, config)
            await asyncio.sleep(5)  # wait 5 seconds before the next query

    # --- Phase 2: monitor TTLs and refresh when expired ---
    while True:
        # Wait 60 seconds between each full check of all queries.
        # asyncio.sleep() yields control back to the event loop during the wait,
        # so the server can still handle incoming requests while we're sleeping.
        await asyncio.sleep(60)

        for name, config in QUERY_REGISTRY.items():
            key = _cache_key(name)
            meta = _cache_meta.get(key)

            if meta is None:
                # No metadata means this query was never cached (e.g. after
                # a server restart when using in-memory cache with no Redis)
                needs_refresh = True
            else:
                age_seconds = time.time() - meta["cached_at"]
                needs_refresh = age_seconds >= meta["ttl"]
                if needs_refresh:
                    logger.info(f"TTL expired for '{name}' — refreshing")

            if needs_refresh:
                await refresh_query(name, config)
                await asyncio.sleep(5)  # rate limit between queries


# ---------------------------------------------------------------------------
# LIFESPAN
# ---------------------------------------------------------------------------
# FastAPI's lifespan context manager lets us run code at startup and shutdown.
# It replaces the older @app.on_event("startup") / @app.on_event("shutdown")
# decorators which are now deprecated.
#
# The function is split in two by the `yield`:
#   - Everything BEFORE yield runs on startup
#   - Everything AFTER yield runs on shutdown
#
# asyncio.create_task() starts background_refresh_loop() as a concurrent task.
# "Concurrent" means it runs alongside the main server — it doesn't block
# the server from handling requests.
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Dune Data API — launching background refresh loop")
    # create_task schedules background_refresh_loop to run concurrently.
    # We save the reference so we can cancel it on shutdown.
    refresh_task = asyncio.create_task(background_refresh_loop())
    yield  # server is running and handling requests here
    # Shutdown: cancel the background task cleanly so it doesn't linger
    refresh_task.cancel()
    logger.info("Shutting down")


# ---------------------------------------------------------------------------
# FASTAPI APP
# ---------------------------------------------------------------------------
# This creates the FastAPI application instance. All configuration, middleware,
# and routes are attached to this object. The lifespan parameter wires up
# our startup/shutdown logic defined above.
app = FastAPI(
    title="Dune Data API",
    description="Raw Dune Analytics data with caching",
    version="1.0.0",
    docs_url="/docs",    # Swagger UI available at /docs
    redoc_url="/redoc",  # ReDoc UI available at /redoc
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS MIDDLEWARE
# ---------------------------------------------------------------------------
# CORS = Cross-Origin Resource Sharing.
# Browsers enforce a "same-origin policy" — by default, a webpage at
# https://mydashboard.com is BLOCKED from making API requests to
# https://myapi.com because they're on different origins (domains).
#
# Adding the CORSMiddleware tells the browser "this API allows requests
# from other origins", which is necessary if any frontend (React app,
# dashboard, etc) will call this API from a browser.
#
# allow_origins=["*"] means "allow requests from ANY domain".
# In production with sensitive data, you'd restrict this to specific domains:
#   allow_origins=["https://mydashboard.com", "https://myapp.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],   # allow GET, POST, DELETE, etc
    allow_headers=["*"],   # allow any request headers
)


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
# Routes are functions that handle HTTP requests to specific URL paths.
# The decorators (@app.get, @app.delete) register each function with FastAPI
# and tell it which HTTP method and path to match.
#
# All routes are async def because they may call async functions like get_data().
# FastAPI handles async routes natively — it runs them on the event loop
# so they don't block other requests.

@app.get("/")
async def root(request: Request):
    """
    Root endpoint — returns a directory of all available endpoints.
    We use request.base_url to build full URLs dynamically so this works
    on both localhost and production without any hardcoding.
    """
    base_url = str(request.base_url).rstrip("/")
    return {
        "message": "Welcome to the Dune Data API!",
        "version": "1.0.0",
        "status": "online",
        "documentation": f"{base_url}/docs",
        "cache_backend": "redis" if USE_REDIS else "in-memory",
        "endpoints": {
            # Build the query URLs dynamically from the registry.
            # If you add a new query to QUERY_REGISTRY, it automatically
            # appears here without any extra code.
            "queries": {
                name: f"{base_url}/data/{name}"
                for name in QUERY_REGISTRY
            },
            "utilities": {
                "list_queries": f"{base_url}/queries",
                "cache_status": f"{base_url}/cache/status",
                "bust_cache": "DELETE /cache/{name}",
                "health": f"{base_url}/health",
            },
        },
        "total_queries": len(QUERY_REGISTRY),
        "note": "All endpoints return raw data exactly as received from Dune Analytics",
    }


@app.get("/queries")
async def list_queries():
    """
    Returns a summary of every registered query — its ID, TTL, and description.
    Useful for API consumers who want to know what data is available.
    """
    return {
        name: {
            "query_id": cfg.query_id,
            "ttl_seconds": cfg.ttl,
            "description": cfg.description,
        }
        for name, cfg in QUERY_REGISTRY.items()
    }


@app.get("/data/{name}")
async def get_query_data(name: str):
    """
    The main data endpoint. Returns the rows for a named query.

    {name} is a path parameter — FastAPI extracts it from the URL automatically.
    For example, GET /data/stablecoins sets name = "stablecoins".

    The response is split into two sections:
      - metadata: information ABOUT the data (freshness, source, row count)
      - rows:     the actual data from Dune
    
    This separation makes it easy for frontends to check freshness before
    processing the data, and keeps the structure clean and predictable.
    """
    if name not in QUERY_REGISTRY:
        # HTTPException tells FastAPI to return an HTTP error response.
        # 404 = "Not Found". The detail string is returned in the response body.
        raise HTTPException(status_code=404, detail=f"Query '{name}' not found. See /queries.")
    try:
        cfg = QUERY_REGISTRY[name]
        key = _cache_key(name)
        data = await get_data(name)        # cache-aside fetch (cache or Dune)
        cache_info = get_cache_info(key)   # freshness metadata
        return {
            "metadata": {
                "query": name,
                "query_id": cfg.query_id,
                "description": cfg.description,
                "source": "Dune Analytics",
                "cache_backend": "redis" if USE_REDIS else "in-memory",
                "row_count": len(data),
                # **cache_info unpacks the dict returned by get_cache_info()
                # directly into this dict, like spreading its keys inline.
                # So is_cached, is_fresh, cache_age_hours, etc all appear
                # at the top level of metadata without nesting.
                **cache_info,
            },
            "rows": data,
        }
    except Exception as e:
        # 500 = "Internal Server Error". We include the error message so
        # it's easier to debug, though in production you may want to hide
        # internal error details from external users.
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cache/status")
async def cache_status():
    """
    Returns the cache status for every registered query — whether it's cached,
    how old it is, and when it will next be refreshed.
    Useful for debugging and monitoring the health of your cache.
    """
    status = {}
    for name, cfg in QUERY_REGISTRY.items():
        key = _cache_key(name)
        info = get_cache_info(key)
        status[name] = {
            "query_id": cfg.query_id,
            "ttl_seconds": cfg.ttl,
            **info,
        }
    return {
        "cache_backend": "redis" if USE_REDIS else "in-memory",
        "queries": status,
    }


@app.delete("/cache/{name}")
async def bust_cache(name: str):
    """
    Deletes the cached data for a named query.
    The background loop will automatically re-fetch and re-cache it
    within the next 60 seconds.

    When would you use this?
    If you've updated the SQL of a Dune query and want the API to pick up
    the new results immediately without waiting for the TTL to expire.
    """
    if name not in QUERY_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Query '{name}' not found.")
    key = _cache_key(name)
    cache_delete(key)
    return {
        "deleted": True,
        "key": key,
        "note": "Background loop will re-warm this query within 60 seconds",
    }


@app.get("/health")
async def health():
    """
    Health check endpoint. Returns basic information about the server's state.
    Commonly used by deployment platforms (Railway, Render, etc) to check
    if the server is running correctly. If this returns 200, the server is up.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "cache_backend": "redis" if USE_REDIS else "in-memory",
        "registered_queries": len(QUERY_REGISTRY),
        "api_keys_configured": {
            "dune": bool(dune_api_key),
            # bool() on a string returns True if the string is non-empty,
            # False if it's None or "". This way we confirm the key is set
            # without exposing the actual key value in the response.
        },
    }

if __name__ == "__main__":
    """
    This block runs if you execute this file locally with `python dune.py`.
    It starts the Uvicorn server to serve the FastAPI app.
    In production, you typically wouldn't run this file directly. Instead,
    you'd use a command like `uvicorn dune:app --host 0.0.0 --port 8000` to start the server, which is more flexible and standard.
    """
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "dune2:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )