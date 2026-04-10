import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from geocoding import GeocodingError, geocode_address


class GeocodingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
