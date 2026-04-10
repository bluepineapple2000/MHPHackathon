from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional


BLOCK_MINUTES = 30
DEFAULT_GRID_PRICE_PER_KWH = 0.35
EPSILON = 1e-6


@dataclass
class Vehicle:
    id: int
    name: str
    battery_kwh: float
    current_soc_pct: float
    min_reserve_pct: float
    efficiency_kwh_per_km: float
    max_speed_kph: float
    max_charge_power_kw: float
    depot_id: int


@dataclass
class Charger:
    id: int
    name: str
    depot_id: int
    power_kw: float
    slot_count: int


@dataclass
class Route:
    id: int
    name: str
    departure_at: datetime
    arrival_at: datetime
    distance_km: float
    required_speed_kph: float
    start_depot_id: int
    end_depot_id: int


@dataclass
class EnergyWindow:
    id: int
    depot_id: int
    start_at: datetime
    end_at: datetime
    solar_kwh_available: float
    buy_price_per_kwh: float
    price_source: str = "forecast"


@dataclass
class RouteAssignment:
    route_id: int
    vehicle_id: int
    start_soc_pct: float
    end_soc_pct: float
    reserve_pct: float
    route_energy_kwh: float
    charging_cost: float


@dataclass
class ChargeSession:
    vehicle_id: int
    charger_id: int
    start_at: datetime
    end_at: datetime
    target_soc_pct: float
    energy_kwh: float
    solar_kwh: float
    grid_kwh: float
    expected_cost: float


@dataclass
class UnservedRoute:
    route_id: int
    reason: str


@dataclass
class PlanResult:
    assignments: List[RouteAssignment]
    charge_sessions: List[ChargeSession]
    unserved_routes: List[UnservedRoute]
    total_cost: float
    served_routes_count: int
    unserved_routes_count: int


@dataclass
class VehicleState:
    vehicle_id: int
    available_at: datetime
    depot_id: int
    energy_kwh: float
    battery_kwh: float
    min_reserve_pct: float


def floor_to_block(moment: datetime) -> datetime:
    minute = (moment.minute // BLOCK_MINUTES) * BLOCK_MINUTES
    return moment.replace(minute=minute, second=0, microsecond=0)


def iter_blocks(start_at: datetime, end_at: datetime) -> List[tuple[datetime, datetime, datetime, float]]:
    if end_at <= start_at:
        return []
    cursor = floor_to_block(start_at)
    blocks = []
    while cursor < end_at:
        block_end = cursor + timedelta(minutes=BLOCK_MINUTES)
        overlap_start = max(start_at, cursor)
        overlap_end = min(end_at, block_end)
        if overlap_end > overlap_start:
            duration_hours = (overlap_end - overlap_start).total_seconds() / 3600
            blocks.append((cursor, overlap_start, overlap_end, duration_hours))
        cursor = block_end
    return blocks


def route_energy_kwh(route: Route, vehicle: Vehicle) -> float:
    return route.distance_km * vehicle.efficiency_kwh_per_km


def effective_route_speed(route: Route) -> float:
    duration_hours = (route.arrival_at - route.departure_at).total_seconds() / 3600
    if duration_hours <= 0:
        return float("inf")
    computed = route.distance_km / duration_hours
    return max(route.required_speed_kph, computed)


def build_vehicle_states(vehicles: List[Vehicle], now: datetime) -> Dict[int, VehicleState]:
    states = {}
    for vehicle in vehicles:
        states[vehicle.id] = VehicleState(
            vehicle_id=vehicle.id,
            available_at=now,
            depot_id=vehicle.depot_id,
            energy_kwh=vehicle.battery_kwh * max(vehicle.current_soc_pct, 0) / 100,
            battery_kwh=vehicle.battery_kwh,
            min_reserve_pct=vehicle.min_reserve_pct,
        )
    return states


def occupancy_key(charger_id: int, block_anchor: datetime) -> tuple[int, datetime]:
    return charger_id, block_anchor


def energy_window_lookup(
    depot_id: int,
    block_anchor: datetime,
    energy_windows_by_key: Dict[tuple[int, datetime], EnergyWindow],
) -> EnergyWindow | None:
    return energy_windows_by_key.get((depot_id, block_anchor))


def solar_usage_key(depot_id: int, block_anchor: datetime) -> tuple[int, datetime]:
    return depot_id, block_anchor


def merge_sessions(
    selected_blocks: List[dict],
    starting_energy_kwh: float,
    battery_kwh: float,
    vehicle_id: int,
) -> List[ChargeSession]:
    if not selected_blocks:
        return []

    selected_blocks = sorted(selected_blocks, key=lambda item: item["start_at"])
    sessions: List[ChargeSession] = []
    current = None
    accumulated_energy = starting_energy_kwh

    for block in selected_blocks:
        if current is None:
            current = deepcopy(block)
        elif current["charger_id"] == block["charger_id"] and current["end_at"] == block["start_at"]:
            current["end_at"] = block["end_at"]
            current["energy_kwh"] += block["energy_kwh"]
            current["solar_kwh"] += block["solar_kwh"]
            current["grid_kwh"] += block["grid_kwh"]
            current["expected_cost"] += block["expected_cost"]
        else:
            accumulated_energy += current["energy_kwh"]
            sessions.append(
                ChargeSession(
                    vehicle_id=vehicle_id,
                    charger_id=current["charger_id"],
                    start_at=current["start_at"],
                    end_at=current["end_at"],
                    target_soc_pct=min(100.0, accumulated_energy / battery_kwh * 100),
                    energy_kwh=current["energy_kwh"],
                    solar_kwh=current["solar_kwh"],
                    grid_kwh=current["grid_kwh"],
                    expected_cost=current["expected_cost"],
                )
            )
            current = deepcopy(block)

    if current is not None:
        accumulated_energy += current["energy_kwh"]
        sessions.append(
            ChargeSession(
                vehicle_id=vehicle_id,
                charger_id=current["charger_id"],
                start_at=current["start_at"],
                end_at=current["end_at"],
                target_soc_pct=min(100.0, accumulated_energy / battery_kwh * 100),
                energy_kwh=current["energy_kwh"],
                solar_kwh=current["solar_kwh"],
                grid_kwh=current["grid_kwh"],
                expected_cost=current["expected_cost"],
            )
        )

    return sessions


def plan_charging(
    state: VehicleState,
    vehicle: Vehicle,
    route: Route,
    chargers_by_depot: Dict[int, List[Charger]],
    energy_windows_by_key: Dict[tuple[int, datetime], EnergyWindow],
    occupancy: Dict[tuple[int, datetime], int],
    solar_usage: Dict[tuple[int, datetime], float],
) -> tuple[bool, List[ChargeSession], float, float, str]:
    reserve_kwh = vehicle.battery_kwh * vehicle.min_reserve_pct / 100
    needed_energy_kwh = route_energy_kwh(route, vehicle) + reserve_kwh
    starting_energy_kwh = state.energy_kwh

    if starting_energy_kwh + EPSILON >= needed_energy_kwh:
        return True, [], 0.0, starting_energy_kwh, ""

    chargers = chargers_by_depot.get(state.depot_id, [])
    if not chargers:
        return False, [], 0.0, starting_energy_kwh, "no chargers available at depot"

    candidate_blocks = []
    for charger in chargers:
        charging_power_kw = min(vehicle.max_charge_power_kw, charger.power_kw)
        if charging_power_kw <= 0:
            continue
        for block_anchor, start_at, end_at, duration_hours in iter_blocks(
            state.available_at, route.departure_at
        ):
            if occupancy.get(occupancy_key(charger.id, block_anchor), 0) >= charger.slot_count:
                continue
            energy_capacity = charging_power_kw * duration_hours
            if energy_capacity <= EPSILON:
                continue
            energy_window = energy_window_lookup(state.depot_id, block_anchor, energy_windows_by_key)
            buy_price = DEFAULT_GRID_PRICE_PER_KWH
            solar_available = 0.0
            if energy_window is not None:
                buy_price = energy_window.buy_price_per_kwh
                already_used = solar_usage.get(solar_usage_key(state.depot_id, block_anchor), 0.0)
                solar_available = max(0.0, energy_window.solar_kwh_available - already_used)
            effective_price = buy_price * max(0.0, energy_capacity - solar_available) / energy_capacity
            candidate_blocks.append(
                {
                    "charger_id": charger.id,
                    "block_anchor": block_anchor,
                    "start_at": start_at,
                    "end_at": end_at,
                    "energy_capacity": energy_capacity,
                    "buy_price_per_kwh": buy_price,
                    "solar_available": solar_available,
                    "effective_price": effective_price,
                }
            )

    if not candidate_blocks:
        return False, [], 0.0, starting_energy_kwh, "no charging time available before departure"

    candidate_blocks.sort(
        key=lambda block: (
            block["effective_price"],
            block["buy_price_per_kwh"],
            -block["solar_available"],
            block["start_at"],
        )
    )

    remaining_kwh = needed_energy_kwh - starting_energy_kwh
    selected_blocks = []
    total_cost = 0.0

    for block in candidate_blocks:
        if remaining_kwh <= EPSILON:
            break
        used_energy = min(remaining_kwh, block["energy_capacity"])
        if used_energy <= EPSILON:
            continue
        solar_kwh = min(used_energy, block["solar_available"])
        grid_kwh = used_energy - solar_kwh
        selected_blocks.append(
            {
                "charger_id": block["charger_id"],
                "block_anchor": block["block_anchor"],
                "start_at": block["start_at"],
                "end_at": block["end_at"],
                "energy_kwh": used_energy,
                "solar_kwh": solar_kwh,
                "grid_kwh": grid_kwh,
                "expected_cost": grid_kwh * block["buy_price_per_kwh"],
            }
        )
        remaining_kwh -= used_energy
        total_cost += grid_kwh * block["buy_price_per_kwh"]

    if remaining_kwh > EPSILON:
        return False, [], 0.0, starting_energy_kwh, "insufficient charging capacity before departure"

    sessions = merge_sessions(selected_blocks, starting_energy_kwh, vehicle.battery_kwh, vehicle.id)
    departure_energy_kwh = starting_energy_kwh + sum(item["energy_kwh"] for item in selected_blocks)
    return True, sessions, total_cost, departure_energy_kwh, ""


def evaluate_vehicle_for_route(
    vehicle: Vehicle,
    state: VehicleState,
    route: Route,
    chargers_by_depot: Dict[int, List[Charger]],
    energy_windows_by_key: Dict[tuple[int, datetime], EnergyWindow],
    occupancy: Dict[tuple[int, datetime], int],
    solar_usage: Dict[tuple[int, datetime], float],
) -> dict:
    if state.depot_id != route.start_depot_id:
        return {"feasible": False, "reason": "vehicle is at a different depot"}
    if state.available_at > route.departure_at:
        return {"feasible": False, "reason": "vehicle is not available before departure"}
    if effective_route_speed(route) - vehicle.max_speed_kph > EPSILON:
        return {"feasible": False, "reason": "vehicle cannot meet route speed requirement"}

    feasible, sessions, charging_cost, departure_energy_kwh, charge_reason = plan_charging(
        state,
        vehicle,
        route,
        chargers_by_depot,
        energy_windows_by_key,
        occupancy,
        solar_usage,
    )
    if not feasible:
        return {"feasible": False, "reason": charge_reason}

    trip_energy_kwh = route_energy_kwh(route, vehicle)
    reserve_kwh = vehicle.battery_kwh * vehicle.min_reserve_pct / 100
    ending_energy_kwh = departure_energy_kwh - trip_energy_kwh
    if ending_energy_kwh + EPSILON < reserve_kwh:
        return {"feasible": False, "reason": "route would end below safety reserve"}

    total_solar_kwh = sum(session.solar_kwh for session in sessions)
    overcharge_kwh = max(0.0, departure_energy_kwh - (trip_energy_kwh + reserve_kwh))
    return {
        "feasible": True,
        "vehicle": vehicle,
        "sessions": sessions,
        "charging_cost": charging_cost,
        "departure_energy_kwh": departure_energy_kwh,
        "ending_energy_kwh": ending_energy_kwh,
        "trip_energy_kwh": trip_energy_kwh,
        "score": (charging_cost, -total_solar_kwh, overcharge_kwh, state.available_at, vehicle.name.lower()),
    }


def commit_sessions(
    depot_id: int,
    sessions: List[ChargeSession],
    occupancy: Dict[tuple[int, datetime], int],
    solar_usage: Dict[tuple[int, datetime], float],
) -> None:
    for session in sessions:
        total_duration_hours = (session.end_at - session.start_at).total_seconds() / 3600
        for block_anchor, overlap_start, overlap_end, _duration_hours in iter_blocks(session.start_at, session.end_at):
            key = occupancy_key(session.charger_id, block_anchor)
            occupancy[key] = occupancy.get(key, 0) + 1
            overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600
            if total_duration_hours > 0 and session.solar_kwh > 0:
                solar_fraction = overlap_hours / total_duration_hours
                solar_key = solar_usage_key(depot_id, block_anchor)
                solar_usage[solar_key] = solar_usage.get(solar_key, 0.0) + session.solar_kwh * solar_fraction


def run_weekly_plan(
    vehicles: List[Vehicle],
    chargers: List[Charger],
    routes: List[Route],
    energy_windows: List[EnergyWindow],
    horizon_start: Optional[datetime] = None,
    horizon_days: int = 7,
) -> PlanResult:
    horizon_start = horizon_start or datetime.now().replace(second=0, microsecond=0)
    horizon_end = horizon_start + timedelta(days=horizon_days)

    filtered_routes = [
        route
        for route in routes
        if horizon_start <= route.departure_at <= horizon_end
    ]
    filtered_routes.sort(key=lambda route: (route.departure_at, route.arrival_at, route.name.lower()))

    chargers_by_depot: Dict[int, List[Charger]] = {}
    for charger in chargers:
        chargers_by_depot.setdefault(charger.depot_id, []).append(charger)

    energy_windows_by_key = {
        (window.depot_id, window.start_at): window
        for window in energy_windows
    }

    states = build_vehicle_states(vehicles, horizon_start)
    occupancy: Dict[tuple[int, datetime], int] = {}
    solar_usage: Dict[tuple[int, datetime], float] = {}
    assignments: List[RouteAssignment] = []
    charge_sessions: List[ChargeSession] = []
    unserved_routes: List[UnservedRoute] = []
    total_cost = 0.0

    for route in filtered_routes:
        best_candidate = None
        reasons = []
        for vehicle in vehicles:
            candidate = evaluate_vehicle_for_route(
                vehicle,
                states[vehicle.id],
                route,
                chargers_by_depot,
                energy_windows_by_key,
                occupancy,
                solar_usage,
            )
            if not candidate["feasible"]:
                reasons.append(candidate["reason"])
                continue
            if best_candidate is None or candidate["score"] < best_candidate["score"]:
                best_candidate = candidate

        if best_candidate is None:
            reason = reasons[0] if reasons else "no feasible vehicle found"
            unserved_routes.append(UnservedRoute(route_id=route.id, reason=reason))
            continue

        chosen_vehicle = best_candidate["vehicle"]
        chosen_state = states[chosen_vehicle.id]
        commit_sessions(chosen_state.depot_id, best_candidate["sessions"], occupancy, solar_usage)
        charge_sessions.extend(best_candidate["sessions"])
        total_cost += best_candidate["charging_cost"]

        assignments.append(
            RouteAssignment(
                route_id=route.id,
                vehicle_id=chosen_vehicle.id,
                start_soc_pct=min(100.0, best_candidate["departure_energy_kwh"] / chosen_vehicle.battery_kwh * 100),
                end_soc_pct=max(0.0, best_candidate["ending_energy_kwh"] / chosen_vehicle.battery_kwh * 100),
                reserve_pct=chosen_vehicle.min_reserve_pct,
                route_energy_kwh=best_candidate["trip_energy_kwh"],
                charging_cost=best_candidate["charging_cost"],
            )
        )

        chosen_state.available_at = route.arrival_at
        chosen_state.depot_id = route.end_depot_id
        chosen_state.energy_kwh = max(0.0, best_candidate["ending_energy_kwh"])

    return PlanResult(
        assignments=assignments,
        charge_sessions=charge_sessions,
        unserved_routes=unserved_routes,
        total_cost=round(total_cost, 2),
        served_routes_count=len(assignments),
        unserved_routes_count=len(unserved_routes),
    )
