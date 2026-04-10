from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving/"
USER_AGENT = "GridPilotHackathon/1.0 (OpenAI Codex demo)"


class RoutingError(RuntimeError):
    pass


def route_through_waypoints(points: list[tuple[float, float]]) -> dict:
    if len(points) < 2:
        raise RoutingError("at least two route points are required")

    coordinates = ";".join(f"{longitude},{latitude}" for latitude, longitude in points)
    url = (
        f"{OSRM_ROUTE_URL}{coordinates}?"
        + urlencode(
            {
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            }
        )
    )
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
        raise RoutingError("could not calculate a street route") from exc

    routes = payload.get("routes") or []
    if not routes:
        raise RoutingError("no drivable route found for the selected stops")

    route = routes[0]
    geometry = route.get("geometry", {})
    coordinates = geometry.get("coordinates") or []
    if not coordinates:
        raise RoutingError("routing service returned no route geometry")

    return {
        "distance_km": float(route["distance"]) / 1000,
        "duration_minutes": float(route["duration"]) / 60,
        "geometry_geojson": geometry,
        "leaflet_path": [[lat, lon] for lon, lat in coordinates],
    }
