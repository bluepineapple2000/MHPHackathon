from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from planner import EnergyWindow


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
AWATTAR_URL = "https://api.awattar.de/v1/marketdata"
DEFAULT_SPOT_PRICE_EUR_PER_KWH = 0.12
DEFAULT_PANEL_TILT = 30.0
DEFAULT_PANEL_AZIMUTH = 180.0
DEFAULT_SOLAR_EFFICIENCY = 0.82
DEFAULT_GRID_FEE = 0.18
DEFAULT_SUPPLIER_MARKUP_PCT = 3.0
DEFAULT_TAX_MULTIPLIER = 1.19


class ForecastError(RuntimeError):
    pass


@dataclass
class DepotEnergyProfile:
    id: int
    name: str
    location: str
    latitude: float
    longitude: float
    solar_capacity_kwp: float
    panel_tilt_deg: float
    panel_azimuth_deg: float
    solar_efficiency_factor: float
    grid_fee_per_kwh: float
    supplier_markup_pct: float
    tax_multiplier: float


def hour_floor(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def fetch_json(url: str, params: dict) -> dict:
    endpoint = f"{url}?{urlencode(params)}"
    try:
        with urlopen(endpoint, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ForecastError(f"could not fetch forecast data from {url}") from exc


def fetch_weather_forecast(
    depot: DepotEnergyProfile,
    horizon_start: datetime,
    horizon_days: int,
) -> dict[datetime, dict]:
    if depot.latitude is None or depot.longitude is None:
        raise ForecastError(f"depot '{depot.name}' is missing coordinates")

    payload = fetch_json(
        OPEN_METEO_URL,
        {
            "latitude": depot.latitude,
            "longitude": depot.longitude,
            "hourly": "shortwave_radiation,cloud_cover",
            "forecast_days": min(max(horizon_days, 1), 16),
            "timezone": "Europe/Berlin",
        },
    )
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    radiation = hourly.get("shortwave_radiation", [])
    cloud_cover = hourly.get("cloud_cover", [])
    if not times or len(times) != len(radiation):
        raise ForecastError(f"weather forecast for depot '{depot.name}' is incomplete")

    weather_by_hour: dict[datetime, dict] = {}
    horizon_end = horizon_start + timedelta(days=horizon_days)
    for index, timestamp in enumerate(times):
        slot_start = datetime.fromisoformat(timestamp)
        if slot_start < hour_floor(horizon_start) or slot_start >= horizon_end:
            continue
        weather_by_hour[slot_start] = {
            "shortwave_radiation": max(0.0, float(radiation[index] or 0.0)),
            "cloud_cover": float(cloud_cover[index] or 0.0) if index < len(cloud_cover) else 0.0,
        }
    return weather_by_hour


def fetch_market_history(
    horizon_start: datetime,
    horizon_days: int,
    history_days: int = 14,
) -> dict[datetime, float]:
    start = hour_floor(horizon_start) - timedelta(days=history_days)
    end = hour_floor(horizon_start) + timedelta(days=max(2, horizon_days) + 1)
    try:
        payload = fetch_json(
            AWATTAR_URL,
            {
                "start": int(start.timestamp() * 1000),
                "end": int(end.timestamp() * 1000),
            },
        )
    except ForecastError:
        return {}

    history: dict[datetime, float] = {}
    for item in payload.get("data", []):
        start_ts = item.get("start_timestamp")
        market_price = item.get("marketprice")
        if start_ts is None or market_price is None:
            continue
        slot_start = datetime.fromtimestamp(start_ts / 1000)
        history[slot_start] = float(market_price) / 1000
    return history


def estimate_spot_price(
    slot_start: datetime,
    depot: DepotEnergyProfile,
    market_history: dict[datetime, float],
    solar_kwh: float,
) -> float:
    all_prices = list(market_history.values())
    same_hour_prices = [
        price
        for timestamp, price in market_history.items()
        if timestamp.hour == slot_start.hour
    ]
    baseline = mean(same_hour_prices) if same_hour_prices else (
        mean(all_prices) if all_prices else DEFAULT_SPOT_PRICE_EUR_PER_KWH
    )

    if 17 <= slot_start.hour < 21:
        time_adjustment = 0.055
    elif 7 <= slot_start.hour < 17:
        time_adjustment = 0.02
    elif 0 <= slot_start.hour < 5:
        time_adjustment = -0.025
    else:
        time_adjustment = -0.01

    weekend_adjustment = -0.015 if slot_start.weekday() >= 5 else 0.0
    solar_ratio = 0.0
    if depot.solar_capacity_kwp > 0:
        solar_ratio = min(1.0, solar_kwh / depot.solar_capacity_kwp)
    solar_adjustment = -0.05 * solar_ratio

    return max(-0.05, baseline + time_adjustment + weekend_adjustment + solar_adjustment)


def solar_output_kwh(
    depot: DepotEnergyProfile,
    shortwave_radiation: float,
    cloud_cover: float,
) -> float:
    if depot.solar_capacity_kwp <= 0:
        return 0.0
    irradiance_factor = max(0.0, shortwave_radiation) / 1000
    cloud_factor = max(0.15, 1.0 - (cloud_cover / 100.0) * 0.45)
    return max(
        0.0,
        irradiance_factor * depot.solar_capacity_kwp * depot.solar_efficiency_factor * cloud_factor,
    )


def build_energy_forecast(
    depots: list[DepotEnergyProfile],
    horizon_start: datetime,
    horizon_days: int = 7,
) -> list[EnergyWindow]:
    market_history = fetch_market_history(horizon_start, horizon_days)
    windows: list[EnergyWindow] = []
    horizon_end = hour_floor(horizon_start) + timedelta(days=horizon_days)
    next_window_id = 1

    for depot in depots:
        weather_by_hour = fetch_weather_forecast(depot, horizon_start, horizon_days)
        cursor = hour_floor(horizon_start)
        while cursor < horizon_end:
            weather = weather_by_hour.get(cursor, {"shortwave_radiation": 0.0, "cloud_cover": 100.0})
            hourly_solar_kwh = solar_output_kwh(
                depot,
                weather["shortwave_radiation"],
                weather["cloud_cover"],
            )
            market_price = market_history.get(cursor)
            price_source = "market-data" if market_price is not None else "historical-weather-heuristic"
            if market_price is None:
                market_price = estimate_spot_price(cursor, depot, market_history, hourly_solar_kwh)

            buy_price_per_kwh = max(
                0.0,
                (
                    depot.grid_fee_per_kwh
                    + market_price * (1 + depot.supplier_markup_pct / 100)
                ) * depot.tax_multiplier,
            )

            for minute_offset in (0, 30):
                block_start = cursor + timedelta(minutes=minute_offset)
                windows.append(
                    EnergyWindow(
                        id=next_window_id,
                        depot_id=depot.id,
                        start_at=block_start,
                        end_at=block_start + timedelta(minutes=30),
                        solar_kwh_available=hourly_solar_kwh / 2,
                        buy_price_per_kwh=buy_price_per_kwh,
                        price_source=price_source,
                    )
                )
                next_window_id += 1
            cursor += timedelta(hours=1)

    return windows
