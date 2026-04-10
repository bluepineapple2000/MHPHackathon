import json
import unittest
from datetime import datetime
from unittest.mock import patch

from forecast import DepotEnergyProfile, build_energy_forecast


class ForecastTests(unittest.TestCase):
    @patch("forecast.urlopen")
    def test_builds_half_hour_windows_from_weather_and_market_data(self, mock_urlopen):
        weather_payload = {
            "hourly": {
                "time": ["2026-04-13T09:00", "2026-04-13T10:00"],
                "shortwave_radiation": [500, 0],
                "cloud_cover": [20, 90],
            }
        }
        market_payload = {
            "data": [
                {"start_timestamp": int(datetime(2026, 4, 13, 9, 0).timestamp() * 1000), "marketprice": 50.0},
                {"start_timestamp": int(datetime(2026, 4, 13, 10, 0).timestamp() * 1000), "marketprice": 80.0},
            ]
        }

        responses = [
            DummyResponse(json.dumps(market_payload)),
            DummyResponse(json.dumps(weather_payload)),
        ]
        mock_urlopen.side_effect = responses

        depot = DepotEnergyProfile(
            id=1,
            name="Central",
            location="Stuttgart",
            latitude=48.7758,
            longitude=9.1829,
            solar_capacity_kwp=100.0,
            panel_tilt_deg=30.0,
            panel_azimuth_deg=180.0,
            solar_efficiency_factor=0.8,
            grid_fee_per_kwh=0.18,
            supplier_markup_pct=3.0,
            tax_multiplier=1.19,
        )

        windows = build_energy_forecast([depot], datetime(2026, 4, 13, 9, 0), horizon_days=1)

        self.assertGreaterEqual(len(windows), 4)
        first = windows[0]
        self.assertEqual(first.start_at, datetime(2026, 4, 13, 9, 0))
        self.assertGreater(first.solar_kwh_available, 0)
        self.assertEqual(first.price_source, "market-data")


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
