from __future__ import annotations

import json
import threading
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "map_cache.json"
MIN_REQUEST_INTERVAL_SECONDS = 1.0

_cache_lock = threading.Lock()
_last_external_request_started_at = 0.0


def _empty_cache() -> dict:
    return {"geocodes": {}, "routes": {}}


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return _empty_cache()
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cache()
    if not isinstance(payload, dict):
        return _empty_cache()
    return {
        "geocodes": payload.get("geocodes", {}),
        "routes": payload.get("routes", {}),
    }


def _write_cache(payload: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CACHE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(CACHE_PATH)


def normalize_address(query: str) -> str:
    return " ".join(query.strip().lower().split())


def route_cache_key(points: list[tuple[float, float]]) -> str:
    return "|".join(f"{latitude:.6f},{longitude:.6f}" for latitude, longitude in points)


def get_cached_geocode(query: str) -> dict | None:
    cache_key = normalize_address(query)
    with _cache_lock:
        value = _load_cache()["geocodes"].get(cache_key)
    return dict(value) if value else None


def store_geocode(query: str, value: dict) -> None:
    cache_key = normalize_address(query)
    with _cache_lock:
        payload = _load_cache()
        payload["geocodes"][cache_key] = value
        _write_cache(payload)


def get_cached_route(points: list[tuple[float, float]]) -> dict | None:
    cache_key = route_cache_key(points)
    with _cache_lock:
        value = _load_cache()["routes"].get(cache_key)
    return dict(value) if value else None


def store_route(points: list[tuple[float, float]], value: dict) -> None:
    cache_key = route_cache_key(points)
    with _cache_lock:
        payload = _load_cache()
        payload["routes"][cache_key] = value
        _write_cache(payload)


def wait_for_map_service_slot() -> None:
    global _last_external_request_started_at

    with _cache_lock:
        now = time.monotonic()
        remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - _last_external_request_started_at)
        if remaining > 0:
            time.sleep(remaining)
        _last_external_request_started_at = time.monotonic()
