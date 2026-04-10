from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "GridPilotHackathon/1.0 (OpenAI Codex demo)"


class GeocodingError(RuntimeError):
    pass


def geocode_address(query: str) -> dict:
    query = query.strip()
    if not query:
        raise GeocodingError("address is required")

    url = f"{NOMINATIM_SEARCH_URL}?{urlencode({'q': query, 'format': 'jsonv2', 'limit': 1})}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GeocodingError("could not geocode the provided address") from exc

    if not payload:
        raise GeocodingError("no map location found for that address")

    first = payload[0]
    return {
        "label": first.get("display_name", query),
        "latitude": float(first["lat"]),
        "longitude": float(first["lon"]),
    }
