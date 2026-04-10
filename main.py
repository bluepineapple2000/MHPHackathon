from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, url_for

from planner import Charger, PriceWindow, Route, Vehicle, run_weekly_plan


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "planner.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "hackathon-prototype"
app.config["DATABASE"] = DATABASE_PATH


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS depots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
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
    created_at TEXT NOT NULL,
    FOREIGN KEY (start_depot_id) REFERENCES depots (id) ON DELETE CASCADE,
    FOREIGN KEY (end_depot_id) REFERENCES depots (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    depot_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    price_per_kwh REAL NOT NULL,
    created_at TEXT NOT NULL,
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


def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.executescript(SCHEMA)
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
    if row is None:
        return None
    return row["id"]


def coverage_ratio(plan_run: sqlite3.Row | None) -> float:
    if plan_run is None:
        return 0.0
    total = plan_run["served_routes_count"] + plan_run["unserved_routes_count"]
    if total == 0:
        return 0.0
    return plan_run["served_routes_count"] / total * 100


@app.template_filter("datetime_display")
def datetime_display(value: str | None) -> str:
    if not value:
        return "n/a"
    return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")


@app.template_filter("money")
def money(value: float | None) -> str:
    if value is None:
        return "EUR 0.00"
    return f"EUR {value:,.2f}"


@app.context_processor
def inject_navigation_state() -> dict:
    return {"latest_plan_id": latest_plan_id()}


@app.route("/")
def home():
    stats = query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM depots) AS depots_count,
            (SELECT COUNT(*) FROM chargers) AS chargers_count,
            (SELECT COUNT(*) FROM vehicles) AS vehicles_count,
            (SELECT COUNT(*) FROM routes) AS routes_count,
            (SELECT COUNT(*) FROM price_windows) AS prices_count
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
    return render_template(
        "index.html",
        stats=stats,
        latest_plan=latest_plan,
        latest_plan_coverage=coverage_ratio(latest_plan),
        upcoming_routes=upcoming_routes,
    )


@app.route("/depots", methods=["GET", "POST"])
def depots():
    if request.method == "POST":
        name = request.form["name"].strip()
        location = request.form["location"].strip()
        if not name or not location:
            flash("Depot name and location are required.", "error")
        else:
            execute(
                "INSERT INTO depots (name, location, created_at) VALUES (?, ?, ?)",
                (name, location, utc_now_iso()),
            )
            flash(f"Depot '{name}' added.", "success")
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
    return render_template("depots.html", depots=depots_list)


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
            arrival_at = parse_datetime_local(request.form["arrival_at"])
        except ValueError:
            flash("Use valid route times.", "error")
            return redirect(url_for("routes"))

        name = request.form["name"].strip()
        distance_km = float(request.form["distance_km"])
        required_speed_kph = float(request.form["required_speed_kph"])
        start_depot_id = int(request.form["start_depot_id"])
        end_depot_id = int(request.form["end_depot_id"])

        if not name or distance_km <= 0 or required_speed_kph <= 0:
            flash("Complete all route fields with valid positive values.", "error")
        elif arrival_at <= departure_at:
            flash("Arrival must be after departure.", "error")
        else:
            execute(
                """
                INSERT INTO routes (
                    name, departure_at, arrival_at, distance_km, required_speed_kph,
                    start_depot_id, end_depot_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    departure_at.isoformat(timespec="minutes"),
                    arrival_at.isoformat(timespec="minutes"),
                    distance_km,
                    required_speed_kph,
                    start_depot_id,
                    end_depot_id,
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
            end_depot.name AS end_depot_name
        FROM routes
        JOIN depots AS start_depot ON start_depot.id = routes.start_depot_id
        JOIN depots AS end_depot ON end_depot.id = routes.end_depot_id
        ORDER BY routes.departure_at ASC
        """
    )
    return render_template("routes.html", routes=route_list, depots=depots_list)


@app.route("/prices", methods=["GET", "POST"])
def prices():
    depots_list = query_all("SELECT * FROM depots ORDER BY name ASC")
    if request.method == "POST":
        if not depots_list:
            flash("Add a depot before entering electricity prices.", "error")
            return redirect(url_for("depots"))
        try:
            start_at = parse_datetime_local(request.form["start_at"])
            end_at = parse_datetime_local(request.form["end_at"])
        except ValueError:
            flash("Use valid price window dates.", "error")
            return redirect(url_for("prices"))

        depot_id = int(request.form["depot_id"])
        price_per_kwh = float(request.form["price_per_kwh"])
        if price_per_kwh <= 0:
            flash("Price per kWh must be positive.", "error")
        elif end_at <= start_at:
            flash("Price window end must be after start.", "error")
        else:
            execute(
                """
                INSERT INTO price_windows (depot_id, start_at, end_at, price_per_kwh, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    depot_id,
                    start_at.isoformat(timespec="minutes"),
                    end_at.isoformat(timespec="minutes"),
                    price_per_kwh,
                    utc_now_iso(),
                ),
            )
            flash("Price window added.", "success")
            return redirect(url_for("prices"))

    price_list = query_all(
        """
        SELECT price_windows.*, depots.name AS depot_name
        FROM price_windows
        JOIN depots ON depots.id = price_windows.depot_id
        ORDER BY price_windows.start_at ASC
        """
    )
    return render_template("prices.html", prices=price_list, depots=depots_list)


@app.post("/plan/run")
def run_plan():
    horizon_days = int(request.form.get("horizon_days", 7))
    vehicles_rows = query_all("SELECT * FROM vehicles ORDER BY name ASC")
    routes_rows = query_all("SELECT * FROM routes ORDER BY departure_at ASC")

    if not vehicles_rows:
        flash("Add at least one vehicle before running the planner.", "error")
        return redirect(url_for("vehicles"))
    if not routes_rows:
        flash("Add at least one route before running the planner.", "error")
        return redirect(url_for("routes"))

    chargers_rows = query_all("SELECT * FROM chargers ORDER BY name ASC")
    prices_rows = query_all("SELECT * FROM price_windows ORDER BY start_at ASC")

    horizon_start = datetime.now().replace(second=0, microsecond=0)
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
    price_windows = [
        PriceWindow(
            id=row["id"],
            depot_id=row["depot_id"],
            start_at=datetime.fromisoformat(row["start_at"]),
            end_at=datetime.fromisoformat(row["end_at"]),
            price_per_kwh=row["price_per_kwh"],
        )
        for row in prices_rows
    ]

    result = run_weekly_plan(
        vehicles=vehicles,
        chargers=chargers,
        routes=routes_to_plan,
        price_windows=price_windows,
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
                target_soc_pct, energy_kwh, expected_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_run_id,
                session.vehicle_id,
                session.charger_id,
                session.start_at.isoformat(timespec="minutes"),
                session.end_at.isoformat(timespec="minutes"),
                session.target_soc_pct,
                session.energy_kwh,
                session.expected_cost,
            ),
        )

    for item in result.unserved_routes:
        execute(
            "INSERT INTO unserved_routes (plan_run_id, route_id, reason) VALUES (?, ?, ?)",
            (plan_run_id, item.route_id, item.reason),
        )

    flash("Weekly plan generated.", "success")
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
            vehicles.name AS vehicle_name
        FROM route_assignments
        JOIN routes ON routes.id = route_assignments.route_id
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
            routes.name AS route_name,
            routes.departure_at
        FROM unserved_routes
        JOIN routes ON routes.id = unserved_routes.route_id
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
    return render_template(
        "plan.html",
        plan_run=plan_run,
        assignments=assignments,
        charge_plans=charge_plans,
        unserved=unserved,
        coverage=coverage_ratio(plan_run),
    )


@app.route("/impressum")
def impressum():
    return render_template("impressum.html")


init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
