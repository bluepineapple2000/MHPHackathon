import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import map_cache


class MapCacheTests(unittest.TestCase):
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

    def test_store_and_get_geocode_normalizes_address(self):
        value = {
            "label": "Albersloher Weg 80, 48155 Münster, Germany",
            "latitude": 51.9504,
            "longitude": 7.6412,
        }

        map_cache.store_geocode("Albersloher Weg 80, 48155 Münster, Germany", value)

        self.assertEqual(
            map_cache.get_cached_geocode("  ALBERSLOHER   WEG 80, 48155 MÜNSTER, GERMANY "),
            value,
        )

    def test_store_and_get_route_roundtrip(self):
        points = [(51.9607, 7.6261), (51.9626, 7.6285), (51.9607, 7.6261)]
        value = {
            "distance_km": 4.8,
            "duration_minutes": 14.5,
            "geometry_geojson": {"type": "LineString", "coordinates": [[7.6261, 51.9607], [7.6285, 51.9626]]},
            "leaflet_path": [[51.9607, 7.6261], [51.9626, 7.6285]],
        }

        map_cache.store_route(points, value)

        self.assertEqual(map_cache.get_cached_route(points), value)

    def test_wait_for_map_service_slot_throttles_to_one_second(self):
        map_cache._last_external_request_started_at = 9.4

        with patch("map_cache.time.monotonic", side_effect=[10.0, 10.1]), patch("map_cache.time.sleep") as sleep:
            map_cache.wait_for_map_service_slot()

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.4, places=2)


if __name__ == "__main__":
    unittest.main()
