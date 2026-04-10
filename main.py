from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, url_for

from forecast import (
    DEFAULT_GRID_FEE,
    DEFAULT_PANEL_AZIMUTH,
    DEFAULT_PANEL_TILT,
    DEFAULT_SOLAR_EFFICIENCY,
    DEFAULT_SUPPLIER_MARKUP_PCT,
    DEFAULT_TAX_MULTIPLIER,
    DepotEnergyProfile,
    ForecastError,
    build_energy_forecast,
)
from geocoding import GeocodingError, geocode_address
from planner import Charger, EnergyWindow, Route, Vehicle, run_weekly_plan
from routing import RoutingError, route_through_waypoints


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "planner.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "hackathon-prototype"
app.config["DATABASE"] = DATABASE_PATH

DEMO_DEPOT_SPECS = (
    (
        "Demo Muenster East Depot",
        "Albersloher Weg 80, 48155 Münster, Germany",
        25.0,
        0.19,
    ),
    (
        "Demo Muenster North Depot",
        "Steinfurter Straße 113, 48149 Münster, Germany",
        18.0,
        0.18,
    ),
)

DEMO_ROUTE_TEMPLATES = (
    (
        "Early Station Connector",
        5,
        20,
        "Demo Muenster East Depot",
        "Demo Muenster North Depot",
        "Berliner Platz 29, 48143 Münster, Germany",
    ),
    (
        "North Clinic Shuttle",
        9,
        10,
        "Demo Muenster North Depot",
        "Demo Muenster North Depot",
        "Albert-Schweitzer-Campus 1, 48149 Münster, Germany",
    ),
    (
        "East Logistics Run",
        12,
        35,
        "Demo Muenster East Depot",
        "Demo Muenster East Depot",
        "Wolbecker Straße 300, 48155 Münster, Germany",
    ),
    (
        "South Stadium Shuttle",
        16,
        45,
        "Demo Muenster North Depot",
        "Demo Muenster East Depot",
        "Hammer Straße 302, 48153 Münster, Germany",
    ),
    (
        "Late Hiltrup Return",
        21,
        10,
        "Demo Muenster East Depot",
        "Demo Muenster East Depot",
        "Marktallee 73, 48165 Münster, Germany",
    ),
)

DEMO_PREFILL_ROUTE = (
    "Demo Muenster East Depot",
    "Demo Muenster East Depot",
    (
        "Domplatz 20, 48143 Münster, Germany",
        "Aegidiimarkt 7, 48143 Münster, Germany",
        "Hafenweg 26B, 48155 Münster, Germany",
    ),
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS depots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    latitude REAL NOT NULL DEFAULT 48.7758,
    longitude REAL NOT NULL DEFAULT 9.1829,
    solar_capacity_kwp REAL NOT NULL DEFAULT 250.0,
    panel_tilt_deg REAL NOT NULL DEFAULT 30.0,
    panel_azimuth_deg REAL NOT NULL DEFAULT 180.0,
    solar_efficiency_factor REAL NOT NULL DEFAULT 0.82,
    grid_fee_per_kwh REAL NOT NULL DEFAULT 0.18,
    supplier_markup_pct REAL NOT NULL DEFAULT 3.0,
    tax_multiplier REAL NOT NULL DEFAULT 1.19,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chargers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    depot_id INTEGER NOT NULL,
    power_kw REAL NOT NULL,
    slot_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (depot_id) REFERENCES depots (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    depot_id INTEGER NOT NULL,
    battery_kwh REAL NOT NULL,
    current_soc_pct REAL NOT NULL,
    min_reserve_pct REAL NOT NULL,
    efficiency_kwh_per_km REAL NOT NULL,
    max_speed_kph REAL NOT NULL,
    max_charge_power_kw REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (depot_id) REFERENCES depots (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    departure_at TEXT NOT NULL,
    arrival_at TEXT NOT NULL,
    distance_km REAL NOT NULL,
    required_speed_kph REAL NOT NULL,
    start_depot_id INTEGER NOT NULL,
    end_depot_id INTEGER NOT NULL,
    service_address TEXT NOT NULL DEFAULT '',
    service_label TEXT NOT NULL DEFAULT '',
    service_latitude REAL NOT NULL DEFAULT 48.7758,
    service_longitude REAL NOT NULL DEFAULT 9.1829,
    service_points_json TEXT NOT NULL DEFAULT '[]',
    service_stop_count INTEGER NOT NULL DEFAULT 1,
    route_duration_minutes REAL NOT NULL DEFAULT 0,
    route_geometry_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (start_depot_id) REFERENCES depots (id) ON DELETE CASCADE,
    FOREIGN KEY (end_depot_id) REFERENCES depots (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS energy_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    depot_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    solar_kwh_available REAL NOT NULL,
    buy_price_per_kwh REAL NOT NULL,
    price_source TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    FOREIGN KEY (depot_id) REFERENCES depots (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    horizon_start TEXT NOT NULL,
    horizon_end TEXT NOT NULL,
    status TEXT NOT NULL,
    total_cost REAL NOT NULL,
    served_routes_count INTEGER NOT NULL,
    unserved_routes_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS route_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_run_id INTEGER NOT NULL,
    route_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    start_soc_pct REAL NOT NULL,
    end_soc_pct REAL NOT NULL,
    reserve_pct REAL NOT NULL,
    route_energy_kwh REAL NOT NULL,
    charging_cost REAL NOT NULL,
    FOREIGN KEY (plan_run_id) REFERENCES plan_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES routes (id) ON DELETE CASCADE,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS charge_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_run_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    charger_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    target_soc_pct REAL NOT NULL,
    energy_kwh REAL NOT NULL,
    solar_kwh REAL NOT NULL DEFAULT 0,
    grid_kwh REAL NOT NULL DEFAULT 0,
    expected_cost REAL NOT NULL,
    FOREIGN KEY (plan_run_id) REFERENCES plan_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles (id) ON DELETE CASCADE,
    FOREIGN KEY (charger_id) REFERENCES chargers (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS unserved_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_run_id INTEGER NOT NULL,
    route_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    FOREIGN KEY (plan_run_id) REFERENCES plan_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES routes (id) ON DELETE CASCADE
);
"""


def utc_now_iso() -> str:
    return datetime.now().replace(second=0, microsecond=0).isoformat(timespec="minutes")


def parse_datetime_local(value: str) -> datetime:
    return datetime.fromisoformat(value)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_exception: Exception | None) -> None:
    database = g.pop("db", None)
    if database is not None:
        database.close()


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.executescript(SCHEMA)
        ensure_column(connection, "depots", "latitude", "latitude REAL NOT NULL DEFAULT 48.7758")
        ensure_column(connection, "depots", "longitude", "longitude REAL NOT NULL DEFAULT 9.1829")
        ensure_column(connection, "depots", "solar_capacity_kwp", "solar_capacity_kwp REAL NOT NULL DEFAULT 250.0")
        ensure_column(connection, "depots", "panel_tilt_deg", "panel_tilt_deg REAL NOT NULL DEFAULT 30.0")
        ensure_column(connection, "depots", "panel_azimuth_deg", "panel_azimuth_deg REAL NOT NULL DEFAULT 180.0")
        ensure_column(connection, "depots", "solar_efficiency_factor", "solar_efficiency_factor REAL NOT NULL DEFAULT 0.82")
        ensure_column(connection, "depots", "grid_fee_per_kwh", "grid_fee_per_kwh REAL NOT NULL DEFAULT 0.18")
        ensure_column(connection, "depots", "supplier_markup_pct", "supplier_markup_pct REAL NOT NULL DEFAULT 3.0")
        ensure_column(connection, "depots", "tax_multiplier", "tax_multiplier REAL NOT NULL DEFAULT 1.19")
        ensure_column(connection, "routes", "service_label", "service_label TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "routes", "service_latitude", "service_latitude REAL NOT NULL DEFAULT 48.7758")
        ensure_column(connection, "routes", "service_longitude", "service_longitude REAL NOT NULL DEFAULT 9.1829")
        ensure_column(connection, "routes", "service_address", "service_address TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "routes", "service_points_json", "service_points_json TEXT NOT NULL DEFAULT '[]'")
        ensure_column(connection, "routes", "service_stop_count", "service_stop_count INTEGER NOT NULL DEFAULT 1")
        ensure_column(connection, "routes", "route_duration_minutes", "route_duration_minutes REAL NOT NULL DEFAULT 0")
        ensure_column(connection, "routes", "route_geometry_json", "route_geometry_json TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "charge_plans", "solar_kwh", "solar_kwh REAL NOT NULL DEFAULT 0")
        ensure_column(connection, "charge_plans", "grid_kwh", "grid_kwh REAL NOT NULL DEFAULT 0")
        connection.commit()
    finally:
        connection.close()


def query_all(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_db().execute(query, params).fetchall()


def query_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_db().execute(query, params).fetchone()


def execute(query: str, params: tuple = ()) -> int:
    cursor = get_db().execute(query, params)
    get_db().commit()
    return cursor.lastrowid


def latest_plan_id() -> int | None:
    row = query_one("SELECT id FROM plan_runs ORDER BY created_at DESC LIMIT 1")
    return row["id"] if row else None


def coverage_ratio(plan_run: sqlite3.Row | None) -> float:
    if plan_run is None:
        return 0.0
    total = plan_run["served_routes_count"] + plan_run["unserved_routes_count"]
    return 0.0 if total == 0 else plan_run["served_routes_count"] / total * 100


@app.template_filter("datetime_display")
def datetime_display(value: str | None) -> str:
    if not value:
        return "n/a"
    return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")


@app.template_filter("money")
def money(value: float | None) -> str:
    return f"EUR {0 if value is None else value:,.2f}"


@app.context_processor
def inject_navigation_state() -> dict:
    return {"latest_plan_id": latest_plan_id()}


def depots_as_profiles(rows: list[sqlite3.Row]) -> list[DepotEnergyProfile]:
    return [
        DepotEnergyProfile(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            solar_capacity_kwp=row["solar_capacity_kwp"],
            panel_tilt_deg=row["panel_tilt_deg"],
            panel_azimuth_deg=row["panel_azimuth_deg"],
            solar_efficiency_factor=row["solar_efficiency_factor"],
            grid_fee_per_kwh=row["grid_fee_per_kwh"],
            supplier_markup_pct=row["supplier_markup_pct"],
            tax_multiplier=row["tax_multiplier"],
        )
        for row in rows
    ]


def persist_energy_forecast(windows: list[EnergyWindow]) -> None:
    db = get_db()
    db.execute("DELETE FROM energy_forecasts")
    generated_at = utc_now_iso()
    for window in windows:
        db.execute(
            """
            INSERT INTO energy_forecasts (
                depot_id, start_at, end_at, solar_kwh_available, buy_price_per_kwh,
                price_source, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                window.depot_id,
                window.start_at.isoformat(timespec="minutes"),
                window.end_at.isoformat(timespec="minutes"),
                window.solar_kwh_available,
                window.buy_price_per_kwh,
                window.price_source,
                generated_at,
            ),
        )
    db.commit()


def reset_demo_dataset() -> None:
    db = get_db()
    for table_name in (
        "unserved_routes",
        "route_assignments",
        "charge_plans",
        "plan_runs",
        "energy_forecasts",
        "routes",
        "vehicles",
        "chargers",
        "depots",
    ):
        db.execute(f"DELETE FROM {table_name}")
    db.execute(
        """
        DELETE FROM sqlite_sequence
        WHERE name IN (
            'depots',
            'chargers',
            'vehicles',
            'routes',
            'energy_forecasts',
            'plan_runs',
            'route_assignments',
            'charge_plans',
            'unserved_routes'
        )
        """
    )
    db.commit()


def prime_demo_map_cache() -> None:
    depot_points = {}
    for depot_name, depot_location, _solar_capacity_kwp, _grid_fee_per_kwh in DEMO_DEPOT_SPECS:
        depot_points[depot_name] = geocode_address(depot_location)

    for _name, _dep_hour, _dep_minute, start_depot_name, end_depot_name, service_address in DEMO_ROUTE_TEMPLATES:
        service_points = [geocode_address(address) for address in parse_service_addresses(service_address)]
        waypoints = [(depot_points[start_depot_name]["latitude"], depot_points[start_depot_name]["longitude"])]
        waypoints.extend((point["latitude"], point["longitude"]) for point in service_points)
        waypoints.append((depot_points[end_depot_name]["latitude"], depot_points[end_depot_name]["longitude"]))
        route_through_waypoints(waypoints)

    prefill_start_depot, prefill_end_depot, prefill_addresses = DEMO_PREFILL_ROUTE
    prefill_points = [geocode_address(address) for address in prefill_addresses]
    prefill_waypoints = [(depot_points[prefill_start_depot]["latitude"], depot_points[prefill_start_depot]["longitude"])]
    prefill_waypoints.extend((point["latitude"], point["longitude"]) for point in prefill_points)
    prefill_waypoints.append((depot_points[prefill_end_depot]["latitude"], depot_points[prefill_end_depot]["longitude"]))
    route_through_waypoints(prefill_waypoints)


def insert_demo_dataset() -> tuple[bool, str]:
    reset_demo_dataset()
    prime_demo_map_cache()

    created_at = utc_now_iso()
    depots = {}
    for depot in DEMO_DEPOT_SPECS:
        geocoded_depot = geocode_address(depot[1])
        depot_id = execute(
            """
            INSERT INTO depots (
                name, location, latitude, longitude, solar_capacity_kwp, panel_tilt_deg,
                panel_azimuth_deg, solar_efficiency_factor, grid_fee_per_kwh,
                supplier_markup_pct, tax_multiplier, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                depot[0],
                depot[1],
                geocoded_depot["latitude"],
                geocoded_depot["longitude"],
                depot[2],
                DEFAULT_PANEL_TILT,
                DEFAULT_PANEL_AZIMUTH,
                DEFAULT_SOLAR_EFFICIENCY,
                depot[3],
                DEFAULT_SUPPLIER_MARKUP_PCT,
                DEFAULT_TAX_MULTIPLIER,
                created_at,
            ),
        )
        depots[depot[0]] = {
            "id": depot_id,
            "latitude": geocoded_depot["latitude"],
            "longitude": geocoded_depot["longitude"],
        }

    for charger in (
        ("East Fast Charger", "Demo Muenster East Depot", 120.0, 1),
        ("East Solar Canopy", "Demo Muenster East Depot", 50.0, 2),
        ("North Fast Charger", "Demo Muenster North Depot", 90.0, 1),
        ("North Yard Charger", "Demo Muenster North Depot", 40.0, 2),
    ):
        execute(
            """
            INSERT INTO chargers (name, depot_id, power_kw, slot_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (charger[0], depots[charger[1]]["id"], charger[2], charger[3], created_at),
        )

    vehicles = (
        ("East Bus 01", "Electric bus", "Demo Muenster East Depot", 240.0, 16.0, 20.0, 1.28, 85.0, 120.0),
        ("East Bus 02", "Electric bus", "Demo Muenster East Depot", 240.0, 14.0, 20.0, 1.30, 85.0, 120.0),
        ("East Van 01", "Electric van", "Demo Muenster East Depot", 75.0, 20.0, 15.0, 0.38, 120.0, 50.0),
        ("North Bus 01", "Electric bus", "Demo Muenster North Depot", 220.0, 18.0, 20.0, 1.22, 85.0, 90.0),
        ("North Shuttle 01", "Electric shuttle", "Demo Muenster North Depot", 110.0, 19.0, 18.0, 0.84, 95.0, 50.0),
    )
    for vehicle in vehicles:
        execute(
            """
            INSERT INTO vehicles (
                name, vehicle_type, depot_id, battery_kwh, current_soc_pct,
                min_reserve_pct, efficiency_kwh_per_km, max_speed_kph,
                max_charge_power_kw, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle[0],
                vehicle[1],
                depots[vehicle[2]]["id"],
                *vehicle[3:],
                created_at,
            ),
        )

    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for day_offset in range(2):
        service_day = tomorrow + timedelta(days=day_offset)
        for name, dep_hour, dep_minute, start_depot_name, end_depot_name, service_address in DEMO_ROUTE_TEMPLATES:
            departure_at = service_day.replace(hour=dep_hour, minute=dep_minute)
            service_addresses = parse_service_addresses(service_address)
            start_depot = {"latitude": depots[start_depot_name]["latitude"], "longitude": depots[start_depot_name]["longitude"]}
            end_depot = {"latitude": depots[end_depot_name]["latitude"], "longitude": depots[end_depot_name]["longitude"]}
            service_points = [geocode_address(address) for address in service_addresses]
            waypoints = [(start_depot["latitude"], start_depot["longitude"])]
            waypoints.extend((point["latitude"], point["longitude"]) for point in service_points)
            waypoints.append((end_depot["latitude"], end_depot["longitude"]))
            route_data = route_through_waypoints(waypoints)
            arrival_at = departure_at + timedelta(minutes=route_data["duration_minutes"])
            distance_km = route_data["distance_km"]
            required_speed_kph = distance_km / max(route_data["duration_minutes"] / 60, 1e-6)
            execute(
                """
                INSERT INTO routes (
                    name, departure_at, arrival_at, distance_km, required_speed_kph,
                    start_depot_id, end_depot_id, service_address, service_label,
                    service_latitude, service_longitude, service_points_json,
                    service_stop_count, route_duration_minutes, route_geometry_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{name} D{day_offset + 1}",
                    departure_at.isoformat(timespec="minutes"),
                    arrival_at.isoformat(timespec="minutes"),
                    distance_km,
                    required_speed_kph,
                    depots[start_depot_name]["id"],
                    depots[end_depot_name]["id"],
                    "\n".join(service_addresses),
                    service_summary_label(service_points),
                    service_points[0]["latitude"],
                    service_points[0]["longitude"],
                    json.dumps(service_points),
                    len(service_points),
                    route_data["duration_minutes"],
                    json.dumps(route_data["geometry_geojson"]),
                    created_at,
                ),
            )

    return True, "Demo dataset reset to baseline."


def load_latest_energy_forecast() -> tuple[list[sqlite3.Row], sqlite3.Row | None]:
    latest = query_one("SELECT MAX(generated_at) AS generated_at FROM energy_forecasts")
    if latest is None or latest["generated_at"] is None:
        return [], None
    rows = query_all(
        """
        SELECT energy_forecasts.*, depots.name AS depot_name
        FROM energy_forecasts
        JOIN depots ON depots.id = energy_forecasts.depot_id
        WHERE energy_forecasts.generated_at = ?
        ORDER BY energy_forecasts.start_at ASC, depots.name ASC
        LIMIT 96
        """,
        (latest["generated_at"],),
    )
    return rows, latest


def parse_service_addresses(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def service_summary_label(service_points: list[dict]) -> str:
    if not service_points:
        return "Service route"
    short_labels = [point["label"].split(",")[0].strip() for point in service_points]
    if len(short_labels) == 1:
        return short_labels[0]
    if len(short_labels) == 2:
        return f"2 stops: {short_labels[0]} + {short_labels[1]}"
    return f"{len(short_labels)} stops: " + ", ".join(short_labels[:3])


def build_route_data_for_addresses(
    start_depot: sqlite3.Row,
    end_depot: sqlite3.Row,
    service_addresses: list[str],
) -> tuple[list[dict], dict]:
    service_points = [geocode_address(address) for address in service_addresses]
    waypoints = [(start_depot["latitude"], start_depot["longitude"])]
    waypoints.extend((point["latitude"], point["longitude"]) for point in service_points)
    waypoints.append((end_depot["latitude"], end_depot["longitude"]))
    route_data = route_through_waypoints(waypoints)
    return service_points, route_data


def depot_map_payload(rows: list[sqlite3.Row]) -> list[dict]:
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "location": row["location"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "solar_capacity_kwp": row["solar_capacity_kwp"],
            "charger_summary": row["charger_summary"] if "charger_summary" in row.keys() else None,
        }
        for row in rows
    ]


def route_map_payload(
    rows: list[sqlite3.Row],
    assignments_by_route: dict[int, dict] | None = None,
    unserved_ids: set[int] | None = None,
) -> list[dict]:
    assignments_by_route = assignments_by_route or {}
    unserved_ids = unserved_ids or set()
    payload = []
    for row in rows:
        route_id = row["route_id"] if "route_id" in row.keys() else row["id"]
        assignment = assignments_by_route.get(route_id)
        payload.append(
            {
                "id": route_id,
                "name": row["name"] if "name" in row.keys() else row["route_name"],
                "departure_at": row["departure_at"],
                "arrival_at": row["arrival_at"],
                "distance_km": row["distance_km"],
                "service_stop_count": row["service_stop_count"] if "service_stop_count" in row.keys() else 1,
                "route_duration_minutes": row["route_duration_minutes"] if "route_duration_minutes" in row.keys() else None,
                "geometry": json.loads(row["route_geometry_json"]) if row["route_geometry_json"] else None,
                "service_points": json.loads(row["service_points_json"]) if row["service_points_json"] else [],
                "start_depot": {
                    "id": row["start_depot_id"],
                    "name": row["start_depot_name"],
                    "latitude": row["start_depot_latitude"],
                    "longitude": row["start_depot_longitude"],
                },
                "end_depot": {
                    "id": row["end_depot_id"],
                    "name": row["end_depot_name"],
                    "latitude": row["end_depot_latitude"],
                    "longitude": row["end_depot_longitude"],
                },
                "service_point": {
                    "label": row["service_label"] or "Service point",
                    "latitude": row["service_latitude"],
                    "longitude": row["service_longitude"],
                },
                "assigned_vehicle": assignment["vehicle_name"] if assignment else None,
                "charging_cost": assignment["charging_cost"] if assignment else None,
                "status": "unserved" if route_id in unserved_ids else ("assigned" if assignment else "planned"),
            }
        )
    return payload


@app.route("/")
def home():
    stats = query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM depots) AS depots_count,
            (SELECT COUNT(*) FROM chargers) AS chargers_count,
            (SELECT COUNT(*) FROM vehicles) AS vehicles_count,
            (SELECT COUNT(*) FROM routes) AS routes_count,
            (SELECT COUNT(*) FROM energy_forecasts) AS forecast_slot_count,
            (SELECT ROUND(COALESCE(SUM(solar_capacity_kwp), 0), 1) FROM depots) AS solar_capacity_kwp
        """
    )
    latest_plan = query_one("SELECT * FROM plan_runs ORDER BY created_at DESC LIMIT 1")
    upcoming_routes = query_all(
        """
        SELECT routes.*, start_depot.name AS start_depot_name, end_depot.name AS end_depot_name
        FROM routes
        JOIN depots AS start_depot ON start_depot.id = routes.start_depot_id
        JOIN depots AS end_depot ON end_depot.id = routes.end_depot_id
        WHERE departure_at >= ?
        ORDER BY departure_at ASC
        LIMIT 6
        """,
        (utc_now_iso(),),
    )
    forecast_rows, latest_forecast = load_latest_energy_forecast()
    solar_preview_kwh = round(sum(row["solar_kwh_available"] for row in forecast_rows), 1) if forecast_rows else 0.0
    return render_template(
        "index.html",
        stats=stats,
        latest_plan=latest_plan,
        latest_plan_coverage=coverage_ratio(latest_plan),
        upcoming_routes=upcoming_routes,
        latest_forecast=latest_forecast,
        solar_preview_kwh=solar_preview_kwh,
        has_demo_data=query_one("SELECT id FROM depots WHERE name = ?", ("Demo Muenster East Depot",)) is not None,
    )


@app.post("/demo/seed")
def seed_demo():
    try:
        created, message = insert_demo_dataset()
    except (GeocodingError, RoutingError) as exc:
        created, message = False, f"Demo dataset could not be created: {exc}."
    flash(message, "success" if created else "error")
    return redirect(url_for("home"))


@app.get("/api/geocode")
def api_geocode():
    query = request.args.get("q", "").strip()
    try:
        result = geocode_address(query)
    except GeocodingError as exc:
        return {"error": str(exc)}, 400
    return result


@app.get("/api/route-preview")
def api_route_preview():
    service_addresses_raw = request.args.get("service_addresses", "").strip()
    departure_at_raw = request.args.get("departure_at", "").strip()
    start_depot_id = request.args.get("start_depot_id", "").strip()
    end_depot_id = request.args.get("end_depot_id", "").strip()
    service_addresses = parse_service_addresses(service_addresses_raw)
    if not service_addresses or not start_depot_id or not end_depot_id:
        return {"error": "start depot, end depot, and at least one service address are required"}, 400

    start_depot = query_one("SELECT * FROM depots WHERE id = ?", (start_depot_id,))
    end_depot = query_one("SELECT * FROM depots WHERE id = ?", (end_depot_id,))
    if start_depot is None or end_depot is None:
        return {"error": "selected depot was not found"}, 400

    try:
        service_points, route_data = build_route_data_for_addresses(start_depot, end_depot, service_addresses)
    except (GeocodingError, RoutingError) as exc:
        return {"error": str(exc)}, 400

    response = {
        "service_label": service_summary_label(service_points),
        "service_latitude": service_points[0]["latitude"],
        "service_longitude": service_points[0]["longitude"],
        "service_points": service_points,
        "service_stop_count": len(service_points),
        "distance_km": round(route_data["distance_km"], 1),
        "duration_minutes": round(route_data["duration_minutes"], 1),
        "geometry": route_data["geometry_geojson"],
    }
    if departure_at_raw:
        try:
            departure_at = parse_datetime_local(departure_at_raw)
        except ValueError:
            return {"error": "departure time is invalid"}, 400
        arrival_at = departure_at + timedelta(minutes=route_data["duration_minutes"])
        response["arrival_at"] = arrival_at.isoformat(timespec="minutes")

    return response


@app.route("/depots", methods=["GET", "POST"])
def depots():
    if request.method == "POST":
        name = request.form["name"].strip()
        location = request.form["location"].strip()
        solar_capacity_kwp = float(request.form["solar_capacity_kwp"])
        panel_tilt_deg = float(request.form["panel_tilt_deg"])
        panel_azimuth_deg = float(request.form["panel_azimuth_deg"])
        solar_efficiency_factor = float(request.form["solar_efficiency_factor"])
        grid_fee_per_kwh = float(request.form["grid_fee_per_kwh"])
        supplier_markup_pct = float(request.form["supplier_markup_pct"])
        tax_multiplier = float(request.form["tax_multiplier"])
        if not name or not location:
            flash("Depot name and location are required.", "error")
        elif solar_capacity_kwp < 0 or solar_efficiency_factor <= 0 or tax_multiplier <= 0:
            flash("Solar capacity and tariff settings must be valid positive values.", "error")
        else:
            try:
                geocoded_location = geocode_address(location)
            except GeocodingError as exc:
                flash(f"Could not locate depot address on the map: {exc}.", "error")
                return redirect(url_for("depots"))
            execute(
                """
                INSERT INTO depots (
                    name, location, latitude, longitude, solar_capacity_kwp, panel_tilt_deg,
                    panel_azimuth_deg, solar_efficiency_factor, grid_fee_per_kwh,
                    supplier_markup_pct, tax_multiplier, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    location,
                    geocoded_location["latitude"],
                    geocoded_location["longitude"],
                    solar_capacity_kwp,
                    panel_tilt_deg,
                    panel_azimuth_deg,
                    solar_efficiency_factor,
                    grid_fee_per_kwh,
                    supplier_markup_pct,
                    tax_multiplier,
                    utc_now_iso(),
                ),
            )
            flash(f"Depot '{name}' added with solar settings.", "success")
            return redirect(url_for("depots"))

    depots_list = query_all(
        """
        SELECT depots.*,
               COUNT(DISTINCT vehicles.id) AS vehicle_count,
               COUNT(DISTINCT chargers.id) AS charger_count
        FROM depots
        LEFT JOIN vehicles ON vehicles.depot_id = depots.id
        LEFT JOIN chargers ON chargers.depot_id = depots.id
        GROUP BY depots.id
        ORDER BY depots.name ASC
        """
    )
    return render_template(
        "depots.html",
        depots=depots_list,
        defaults={
            "panel_tilt_deg": DEFAULT_PANEL_TILT,
            "panel_azimuth_deg": DEFAULT_PANEL_AZIMUTH,
            "solar_efficiency_factor": DEFAULT_SOLAR_EFFICIENCY,
            "grid_fee_per_kwh": DEFAULT_GRID_FEE,
            "supplier_markup_pct": DEFAULT_SUPPLIER_MARKUP_PCT,
            "tax_multiplier": DEFAULT_TAX_MULTIPLIER,
        },
    )


@app.route("/chargers", methods=["GET", "POST"])
def chargers():
    depots_list = query_all("SELECT * FROM depots ORDER BY name ASC")
    if request.method == "POST":
        if not depots_list:
            flash("Add a depot before creating chargers.", "error")
            return redirect(url_for("depots"))
        name = request.form["name"].strip()
        depot_id = int(request.form["depot_id"])
        power_kw = float(request.form["power_kw"])
        slot_count = int(request.form["slot_count"])
        if not name or power_kw <= 0 or slot_count <= 0:
            flash("Charger name, power, and slot count must be valid.", "error")
        else:
            execute(
                """
                INSERT INTO chargers (name, depot_id, power_kw, slot_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, depot_id, power_kw, slot_count, utc_now_iso()),
            )
            flash(f"Charger '{name}' added.", "success")
            return redirect(url_for("chargers"))

    charger_list = query_all(
        """
        SELECT chargers.*, depots.name AS depot_name
        FROM chargers
        JOIN depots ON depots.id = chargers.depot_id
        ORDER BY depots.name ASC, chargers.name ASC
        """
    )
    return render_template("chargers.html", chargers=charger_list, depots=depots_list)


@app.route("/vehicles", methods=["GET", "POST"])
def vehicles():
    depots_list = query_all("SELECT * FROM depots ORDER BY name ASC")
    if request.method == "POST":
        if not depots_list:
            flash("Add a depot before creating vehicles.", "error")
            return redirect(url_for("depots"))
        name = request.form["name"].strip()
        vehicle_type = request.form["vehicle_type"].strip()
        depot_id = int(request.form["depot_id"])
        battery_kwh = float(request.form["battery_kwh"])
        current_soc_pct = float(request.form["current_soc_pct"])
        min_reserve_pct = float(request.form["min_reserve_pct"])
        efficiency_kwh_per_km = float(request.form["efficiency_kwh_per_km"])
        max_speed_kph = float(request.form["max_speed_kph"])
        max_charge_power_kw = float(request.form["max_charge_power_kw"])
        numeric_values = [
            battery_kwh,
            current_soc_pct,
            min_reserve_pct,
            efficiency_kwh_per_km,
            max_speed_kph,
            max_charge_power_kw,
        ]
        if not name or not vehicle_type or any(value <= 0 for value in numeric_values):
            flash("Complete all vehicle fields with valid positive values.", "error")
        elif current_soc_pct > 100 or min_reserve_pct >= 100:
            flash("SOC must be <= 100 and reserve must stay below 100.", "error")
        else:
            execute(
                """
                INSERT INTO vehicles (
                    name, vehicle_type, depot_id, battery_kwh, current_soc_pct,
                    min_reserve_pct, efficiency_kwh_per_km, max_speed_kph,
                    max_charge_power_kw, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    vehicle_type,
                    depot_id,
                    battery_kwh,
                    current_soc_pct,
                    min_reserve_pct,
                    efficiency_kwh_per_km,
                    max_speed_kph,
                    max_charge_power_kw,
                    utc_now_iso(),
                ),
            )
            flash(f"Vehicle '{name}' added.", "success")
            return redirect(url_for("vehicles"))

    vehicle_list = query_all(
        """
        SELECT vehicles.*, depots.name AS depot_name
        FROM vehicles
        JOIN depots ON depots.id = vehicles.depot_id
        ORDER BY vehicles.name ASC
        """
    )
    return render_template("vehicles.html", vehicles=vehicle_list, depots=depots_list)


@app.route("/routes", methods=["GET", "POST"])
def routes():
    depots_list = query_all("SELECT * FROM depots ORDER BY name ASC")
    if request.method == "POST":
        if not depots_list:
            flash("Add depots before creating routes.", "error")
            return redirect(url_for("depots"))
        try:
            departure_at = parse_datetime_local(request.form["departure_at"])
        except ValueError:
            flash("Use a valid departure time.", "error")
            return redirect(url_for("routes"))

        name = request.form["name"].strip()
        start_depot_id = int(request.form["start_depot_id"])
        end_depot_id = int(request.form["end_depot_id"])
        service_addresses_raw = request.form["service_addresses"].strip()
        service_addresses = parse_service_addresses(service_addresses_raw)

        if not name:
            flash("Route name is required.", "error")
        elif not service_addresses:
            flash("Add at least one service address for the route map.", "error")
        else:
            start_depot = query_one("SELECT * FROM depots WHERE id = ?", (start_depot_id,))
            end_depot = query_one("SELECT * FROM depots WHERE id = ?", (end_depot_id,))
            try:
                service_points, route_data = build_route_data_for_addresses(
                    start_depot,
                    end_depot,
                    service_addresses,
                )
            except GeocodingError as exc:
                flash(f"Could not locate one of the service addresses on the map: {exc}.", "error")
                return redirect(url_for("routes"))
            except RoutingError as exc:
                flash(f"Could not calculate a drivable street route: {exc}.", "error")
                return redirect(url_for("routes"))

            arrival_at = departure_at + timedelta(minutes=route_data["duration_minutes"])
            distance_km = route_data["distance_km"]
            duration_hours = max(route_data["duration_minutes"] / 60, 1e-6)
            required_speed_kph = distance_km / duration_hours
            execute(
                """
                INSERT INTO routes (
                    name, departure_at, arrival_at, distance_km, required_speed_kph,
                    start_depot_id, end_depot_id, service_address, service_label,
                    service_latitude, service_longitude, service_points_json,
                    service_stop_count, route_duration_minutes, route_geometry_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    departure_at.isoformat(timespec="minutes"),
                    arrival_at.isoformat(timespec="minutes"),
                    distance_km,
                    required_speed_kph,
                    start_depot_id,
                    end_depot_id,
                    service_addresses_raw,
                    service_summary_label(service_points),
                    service_points[0]["latitude"],
                    service_points[0]["longitude"],
                    json.dumps(service_points),
                    len(service_points),
                    route_data["duration_minutes"],
                    json.dumps(route_data["geometry_geojson"]),
                    utc_now_iso(),
                ),
            )
            flash(f"Route '{name}' added.", "success")
            return redirect(url_for("routes"))

    route_list = query_all(
        """
        SELECT
            routes.*,
            start_depot.name AS start_depot_name,
            start_depot.latitude AS start_depot_latitude,
            start_depot.longitude AS start_depot_longitude,
            end_depot.name AS end_depot_name,
            end_depot.latitude AS end_depot_latitude,
            end_depot.longitude AS end_depot_longitude
        FROM routes
        JOIN depots AS start_depot ON start_depot.id = routes.start_depot_id
        JOIN depots AS end_depot ON end_depot.id = routes.end_depot_id
        ORDER BY routes.departure_at ASC
        """
    )
    return render_template(
        "routes.html",
        routes=route_list,
        depots=depots_list,
        depots_map_data=depot_map_payload(depots_list),
        routes_map_data=route_map_payload(route_list),
    )


@app.route("/energy", methods=["GET", "POST"])
def energy():
    depots_rows = query_all("SELECT * FROM depots ORDER BY name ASC")
    if request.method == "POST":
        if not depots_rows:
            flash("Add at least one depot with solar data before fetching forecasts.", "error")
            return redirect(url_for("depots"))
        try:
            windows = build_energy_forecast(
                depots_as_profiles(depots_rows),
                horizon_start=datetime.now().replace(second=0, microsecond=0),
                horizon_days=7,
            )
        except ForecastError as exc:
            flash(f"Automatic forecast failed: {exc}.", "error")
            return redirect(url_for("energy"))

        persist_energy_forecast(windows)
        flash("Solar and market-based energy forecast refreshed.", "success")
        return redirect(url_for("energy"))

    forecast_rows, latest_forecast = load_latest_energy_forecast()
    summary = query_one(
        """
        SELECT
            ROUND(COALESCE(SUM(solar_kwh_available), 0), 1) AS solar_total,
            ROUND(COALESCE(AVG(buy_price_per_kwh), 0), 3) AS avg_buy_price
        FROM energy_forecasts
        WHERE generated_at = ?
        """,
        (latest_forecast["generated_at"],),
    ) if latest_forecast else None
    return render_template(
        "energy.html",
        depots=depots_rows,
        forecast_rows=forecast_rows,
        latest_forecast=latest_forecast,
        summary=summary,
    )


@app.route("/prices")
def prices_redirect():
    return redirect(url_for("energy"))


@app.post("/plan/run")
def run_plan():
    horizon_days = int(request.form.get("horizon_days", 7))
    depots_rows = query_all("SELECT * FROM depots ORDER BY name ASC")
    vehicles_rows = query_all("SELECT * FROM vehicles ORDER BY name ASC")
    routes_rows = query_all("SELECT * FROM routes ORDER BY departure_at ASC")
    chargers_rows = query_all("SELECT * FROM chargers ORDER BY name ASC")

    if not depots_rows:
        flash("Add at least one depot with solar settings before running the planner.", "error")
        return redirect(url_for("depots"))
    if not vehicles_rows:
        flash("Add at least one vehicle before running the planner.", "error")
        return redirect(url_for("vehicles"))
    if not routes_rows:
        flash("Add at least one route before running the planner.", "error")
        return redirect(url_for("routes"))

    horizon_start = datetime.now().replace(second=0, microsecond=0)
    try:
        energy_windows = build_energy_forecast(
            depots_as_profiles(depots_rows),
            horizon_start=horizon_start,
            horizon_days=horizon_days,
        )
    except ForecastError as exc:
        flash(f"Could not build energy forecast: {exc}.", "error")
        return redirect(url_for("energy"))

    persist_energy_forecast(energy_windows)
    vehicles = [
        Vehicle(
            id=row["id"],
            name=row["name"],
            battery_kwh=row["battery_kwh"],
            current_soc_pct=row["current_soc_pct"],
            min_reserve_pct=row["min_reserve_pct"],
            efficiency_kwh_per_km=row["efficiency_kwh_per_km"],
            max_speed_kph=row["max_speed_kph"],
            max_charge_power_kw=row["max_charge_power_kw"],
            depot_id=row["depot_id"],
        )
        for row in vehicles_rows
    ]
    chargers = [
        Charger(
            id=row["id"],
            name=row["name"],
            depot_id=row["depot_id"],
            power_kw=row["power_kw"],
            slot_count=row["slot_count"],
        )
        for row in chargers_rows
    ]
    routes_to_plan = [
        Route(
            id=row["id"],
            name=row["name"],
            departure_at=datetime.fromisoformat(row["departure_at"]),
            arrival_at=datetime.fromisoformat(row["arrival_at"]),
            distance_km=row["distance_km"],
            required_speed_kph=row["required_speed_kph"],
            start_depot_id=row["start_depot_id"],
            end_depot_id=row["end_depot_id"],
        )
        for row in routes_rows
    ]

    result = run_weekly_plan(
        vehicles=vehicles,
        chargers=chargers,
        routes=routes_to_plan,
        energy_windows=energy_windows,
        horizon_start=horizon_start,
        horizon_days=horizon_days,
    )

    total_routes_in_horizon = sum(
        1
        for route in routes_to_plan
        if horizon_start <= route.departure_at <= horizon_start + timedelta(days=horizon_days)
    )
    if total_routes_in_horizon == 0:
        flash("No routes fall inside the planning horizon.", "error")
        return redirect(url_for("routes"))

    plan_run_id = execute(
        """
        INSERT INTO plan_runs (
            created_at, horizon_start, horizon_end, status, total_cost,
            served_routes_count, unserved_routes_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            horizon_start.isoformat(timespec="minutes"),
            (horizon_start + timedelta(days=horizon_days)).isoformat(timespec="minutes"),
            "completed",
            result.total_cost,
            result.served_routes_count,
            result.unserved_routes_count,
        ),
    )

    for assignment in result.assignments:
        execute(
            """
            INSERT INTO route_assignments (
                plan_run_id, route_id, vehicle_id, start_soc_pct, end_soc_pct,
                reserve_pct, route_energy_kwh, charging_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_run_id,
                assignment.route_id,
                assignment.vehicle_id,
                assignment.start_soc_pct,
                assignment.end_soc_pct,
                assignment.reserve_pct,
                assignment.route_energy_kwh,
                assignment.charging_cost,
            ),
        )

    for session in result.charge_sessions:
        execute(
            """
            INSERT INTO charge_plans (
                plan_run_id, vehicle_id, charger_id, start_at, end_at,
                target_soc_pct, energy_kwh, solar_kwh, grid_kwh, expected_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_run_id,
                session.vehicle_id,
                session.charger_id,
                session.start_at.isoformat(timespec="minutes"),
                session.end_at.isoformat(timespec="minutes"),
                session.target_soc_pct,
                session.energy_kwh,
                session.solar_kwh,
                session.grid_kwh,
                session.expected_cost,
            ),
        )

    for item in result.unserved_routes:
        execute(
            "INSERT INTO unserved_routes (plan_run_id, route_id, reason) VALUES (?, ?, ?)",
            (plan_run_id, item.route_id, item.reason),
        )

    flash("Weekly plan generated with solar and market-based energy forecast.", "success")
    return redirect(url_for("plan_detail", plan_id=plan_run_id))


def fetch_plan(plan_id: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
    plan_run = query_one("SELECT * FROM plan_runs WHERE id = ?", (plan_id,))
    if plan_run is None:
        return None, [], [], []

    assignments = query_all(
        """
        SELECT
            route_assignments.*,
            routes.name AS route_name,
            routes.departure_at,
            routes.arrival_at,
            routes.distance_km,
            routes.route_duration_minutes,
            routes.route_geometry_json,
            routes.service_points_json,
            routes.service_stop_count,
            routes.service_label,
            routes.service_latitude,
            routes.service_longitude,
            routes.start_depot_id,
            start_depot.name AS start_depot_name,
            start_depot.latitude AS start_depot_latitude,
            start_depot.longitude AS start_depot_longitude,
            routes.end_depot_id,
            end_depot.name AS end_depot_name,
            end_depot.latitude AS end_depot_latitude,
            end_depot.longitude AS end_depot_longitude,
            vehicles.name AS vehicle_name
        FROM route_assignments
        JOIN routes ON routes.id = route_assignments.route_id
        JOIN depots AS start_depot ON start_depot.id = routes.start_depot_id
        JOIN depots AS end_depot ON end_depot.id = routes.end_depot_id
        JOIN vehicles ON vehicles.id = route_assignments.vehicle_id
        WHERE route_assignments.plan_run_id = ?
        ORDER BY routes.departure_at ASC
        """,
        (plan_id,),
    )
    charge_plans = query_all(
        """
        SELECT
            charge_plans.*,
            vehicles.name AS vehicle_name,
            chargers.name AS charger_name
        FROM charge_plans
        JOIN vehicles ON vehicles.id = charge_plans.vehicle_id
        JOIN chargers ON chargers.id = charge_plans.charger_id
        WHERE charge_plans.plan_run_id = ?
        ORDER BY charge_plans.start_at ASC
        """,
        (plan_id,),
    )
    unserved = query_all(
        """
        SELECT
            unserved_routes.*,
            routes.id AS route_id,
            routes.name AS route_name,
            routes.departure_at,
            routes.arrival_at,
            routes.distance_km,
            routes.route_duration_minutes,
            routes.route_geometry_json,
            routes.service_points_json,
            routes.service_stop_count,
            routes.service_label,
            routes.service_latitude,
            routes.service_longitude,
            routes.start_depot_id,
            start_depot.name AS start_depot_name,
            start_depot.latitude AS start_depot_latitude,
            start_depot.longitude AS start_depot_longitude,
            routes.end_depot_id,
            end_depot.name AS end_depot_name,
            end_depot.latitude AS end_depot_latitude,
            end_depot.longitude AS end_depot_longitude
        FROM unserved_routes
        JOIN routes ON routes.id = unserved_routes.route_id
        JOIN depots AS start_depot ON start_depot.id = routes.start_depot_id
        JOIN depots AS end_depot ON end_depot.id = routes.end_depot_id
        WHERE unserved_routes.plan_run_id = ?
        ORDER BY routes.departure_at ASC
        """,
        (plan_id,),
    )
    return plan_run, assignments, charge_plans, unserved


@app.route("/plan/latest")
def plan_latest():
    plan_id = latest_plan_id()
    if plan_id is None:
        flash("No plan has been generated yet.", "error")
        return redirect(url_for("home"))
    return redirect(url_for("plan_detail", plan_id=plan_id))


@app.route("/plan/<int:plan_id>")
def plan_detail(plan_id: int):
    plan_run, assignments, charge_plans, unserved = fetch_plan(plan_id)
    if plan_run is None:
        flash("Plan not found.", "error")
        return redirect(url_for("home"))
    total_solar_kwh = round(sum(row["solar_kwh"] for row in charge_plans), 1) if charge_plans else 0.0
    total_grid_kwh = round(sum(row["grid_kwh"] for row in charge_plans), 1) if charge_plans else 0.0
    depots_rows = query_all(
        """
        SELECT depots.*,
               GROUP_CONCAT(chargers.name || ' (' || chargers.power_kw || ' kW x' || chargers.slot_count || ')', '; ') AS charger_summary
        FROM depots
        LEFT JOIN chargers ON chargers.depot_id = depots.id
        GROUP BY depots.id
        ORDER BY depots.name ASC
        """
    )
    chargers_rows = query_all(
        """
        SELECT chargers.*, depots.latitude AS depot_latitude, depots.longitude AS depot_longitude, depots.name AS depot_name
        FROM chargers
        JOIN depots ON depots.id = chargers.depot_id
        ORDER BY depots.name ASC, chargers.name ASC
        """
    )
    assignment_lookup = {
        row["route_id"]: {"vehicle_name": row["vehicle_name"], "charging_cost": row["charging_cost"]}
        for row in assignments
    }
    plan_routes = route_map_payload(assignments, assignments_by_route=assignment_lookup)
    plan_routes.extend(route_map_payload(unserved, unserved_ids={row["route_id"] for row in unserved}))
    return render_template(
        "plan.html",
        plan_run=plan_run,
        assignments=assignments,
        charge_plans=charge_plans,
        unserved=unserved,
        coverage=coverage_ratio(plan_run),
        total_solar_kwh=total_solar_kwh,
        total_grid_kwh=total_grid_kwh,
        depots_map_data=depot_map_payload(depots_rows),
        chargers_map_data=[
            {
                "id": row["id"],
                "name": row["name"],
                "depot_id": row["depot_id"],
                "depot_name": row["depot_name"],
                "power_kw": row["power_kw"],
                "slot_count": row["slot_count"],
                "latitude": row["depot_latitude"],
                "longitude": row["depot_longitude"],
            }
            for row in chargers_rows
        ],
        plan_routes_map_data=plan_routes,
    )


@app.route("/impressum")
def impressum():
    return render_template("impressum.html")


init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
