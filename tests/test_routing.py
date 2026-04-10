import json
import tempfile
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

import map_cache
from routing import RoutingError, route_through_waypoints


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.cache_path_patch = patch.object(
            map_cache,
            "CACHE_PATH",
            Path(self.tempdir.name) / "map_cache.json",
        )
        self.cache_path_patch.start()
        self.addCleanup(self.cache_path_patch.stop)
        map_cache._last_external_request_started_at = 0.0

    @patch("routing.urlopen")
    def test_route_through_waypoints_returns_distance_duration_and_geometry(self, mock_urlopen):
        payload = {
            "routes": [
                {
                    "distance": 12450.0,
                    "duration": 1620.0,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [9.1829, 48.7758],
                            [9.1770, 48.7829],
                            [9.1829, 48.7758],
                        ],
                    },
                }
            ]
        }
        mock_urlopen.return_value = DummyResponse(json.dumps(payload))

        result = route_through_waypoints(
            [
                (48.7758, 9.1829),
                (48.7829, 9.1770),
                (48.7758, 9.1829),
            ]
        )

        self.assertAlmostEqual(result["distance_km"], 12.45)
        self.assertAlmostEqual(result["duration_minutes"], 27.0)
        self.assertEqual(len(result["leaflet_path"]), 3)

    @patch("routing.urlopen")
    def test_route_through_waypoints_surfaces_rate_limit_error(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            url="https://router.project-osrm.org/route/v1/driving/",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(
            RoutingError,
            "routing service rate-limited this app \\(HTTP 429 Too Many Requests\\)",
        ):
            route_through_waypoints(
                [
                    (48.7758, 9.1829),
                    (48.7829, 9.1770),
                ]
            )

    @patch("routing.urlopen")
    def test_route_through_waypoints_uses_cached_value_before_network(self, mock_urlopen):
        points = [
            (48.7758, 9.1829),
            (48.7829, 9.1770),
        ]
        cached = {
            "distance_km": 1.25,
            "duration_minutes": 3.8,
            "geometry_geojson": {"type": "LineString", "coordinates": [[9.1829, 48.7758], [9.1770, 48.7829]]},
            "leaflet_path": [[48.7758, 9.1829], [48.7829, 9.1770]],
        }
        map_cache.store_route(points, cached)

        result = route_through_waypoints(points)

        self.assertEqual(result, cached)
        mock_urlopen.assert_not_called()


class DummyResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


if __name__ == "__main__":
    unittest.main()
