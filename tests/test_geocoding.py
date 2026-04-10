import tempfile
import unittest
from urllib.error import HTTPError, URLError
from pathlib import Path
from unittest.mock import patch

import map_cache
from geocoding import GeocodingError, geocode_address


class GeocodingTests(unittest.TestCase):
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

    @patch("geocoding.urlopen")
    def test_geocode_address_surfaces_rate_limit_error(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            url="https://nominatim.openstreetmap.org/search",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(
            GeocodingError,
            "geocoding service rate-limited this app \\(HTTP 429 Too Many Requests\\)",
        ):
            geocode_address("Albersloher Weg 80, 48155 Münster, Germany")

    @patch("geocoding.urlopen")
    def test_geocode_address_surfaces_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("temporary failure in name resolution")

        with self.assertRaisesRegex(
            GeocodingError,
            "geocoding service is unreachable: temporary failure in name resolution",
        ):
            geocode_address("Albersloher Weg 80, 48155 Münster, Germany")

    @patch("geocoding.urlopen")
    def test_geocode_address_uses_cached_value_before_network(self, mock_urlopen):
        cached = {
            "label": "Albersloher Weg 80, 48155 Münster, Germany",
            "latitude": 51.9504,
            "longitude": 7.6412,
        }
        map_cache.store_geocode("Albersloher Weg 80, 48155 Münster, Germany", cached)

        result = geocode_address("Albersloher Weg 80, 48155 Münster, Germany")

        self.assertEqual(result, cached)
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
