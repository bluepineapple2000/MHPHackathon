from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from map_cache import get_cached_geocode, store_geocode, wait_for_map_service_slot


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "GridPilotHackathon/1.0 (OpenAI Codex demo)"


class GeocodingError(RuntimeError):
    pass


def _format_http_error(exc: HTTPError) -> str:
    if exc.code == 429:
        return "geocoding service rate-limited this app (HTTP 429 Too Many Requests)"
    return f"geocoding service failed with HTTP {exc.code} {exc.reason}"


def geocode_address(query: str) -> dict:
    query = query.strip()
    if not query:
        raise GeocodingError("address is required")
    cached = get_cached_geocode(query)
    if cached is not None:
        return cached

    url = f"{NOMINATIM_SEARCH_URL}?{urlencode({'q': query, 'format': 'jsonv2', 'limit': 1})}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        wait_for_map_service_slot()
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GeocodingError(_format_http_error(exc)) from exc
    except URLError as exc:
        raise GeocodingError(f"geocoding service is unreachable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GeocodingError("geocoding service timed out") from exc
    except json.JSONDecodeError as exc:
        raise GeocodingError("geocoding service returned invalid JSON") from exc

    if not payload:
        raise GeocodingError("no map location found for that address")

    first = payload[0]
    result = {
        "label": first.get("display_name", query),
        "latitude": float(first["lat"]),
        "longitude": float(first["lon"]),
    }
    store_geocode(query, result)
    return result
