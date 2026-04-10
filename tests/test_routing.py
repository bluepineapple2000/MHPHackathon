import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from routing import RoutingError, route_through_waypoints


class RoutingTests(unittest.TestCase):
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
