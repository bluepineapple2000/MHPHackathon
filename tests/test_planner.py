import unittest
from datetime import datetime

from planner import Charger, PriceWindow, Route, Vehicle, run_weekly_plan


class PlannerTests(unittest.TestCase):
    def test_assigns_route_without_unneeded_charging(self):
        start = datetime(2026, 4, 13, 8, 0)
        vehicles = [
            Vehicle(
                id=1,
                name="Bus 1",
                battery_kwh=100,
                current_soc_pct=60,
                min_reserve_pct=20,
                efficiency_kwh_per_km=0.5,
                max_speed_kph=90,
                max_charge_power_kw=100,
                depot_id=1,
            )
        ]
        routes = [
            Route(
                id=1,
                name="Morning Route",
                departure_at=datetime(2026, 4, 13, 9, 0),
                arrival_at=datetime(2026, 4, 13, 10, 0),
                distance_km=60,
                required_speed_kph=60,
                start_depot_id=1,
                end_depot_id=1,
            )
        ]

        result = run_weekly_plan(
            vehicles=vehicles,
            chargers=[],
            routes=routes,
            price_windows=[],
            horizon_start=start,
            horizon_days=7,
        )

        self.assertEqual(result.served_routes_count, 1)
        self.assertEqual(len(result.charge_sessions), 0)
        self.assertAlmostEqual(result.assignments[0].start_soc_pct, 60.0)
        self.assertAlmostEqual(result.assignments[0].end_soc_pct, 30.0)

    def test_prefers_cheaper_charging_window_and_stops_at_needed_soc(self):
        start = datetime(2026, 4, 13, 9, 0)
        vehicles = [
            Vehicle(
                id=1,
                name="Bus 2",
                battery_kwh=100,
                current_soc_pct=20,
                min_reserve_pct=20,
                efficiency_kwh_per_km=0.5,
                max_speed_kph=90,
                max_charge_power_kw=60,
                depot_id=1,
            )
        ]
        chargers = [Charger(id=1, name="Fast Charger", depot_id=1, power_kw=60, slot_count=1)]
        routes = [
            Route(
                id=1,
                name="Late Route",
                departure_at=datetime(2026, 4, 13, 11, 30),
                arrival_at=datetime(2026, 4, 13, 12, 30),
                distance_km=60,
                required_speed_kph=60,
                start_depot_id=1,
                end_depot_id=1,
            )
        ]
        prices = [
            PriceWindow(
                id=1,
                depot_id=1,
                start_at=datetime(2026, 4, 13, 9, 0),
                end_at=datetime(2026, 4, 13, 10, 0),
                price_per_kwh=0.45,
            ),
            PriceWindow(
                id=2,
                depot_id=1,
                start_at=datetime(2026, 4, 13, 10, 0),
                end_at=datetime(2026, 4, 13, 11, 0),
                price_per_kwh=0.20,
            ),
        ]

        result = run_weekly_plan(
            vehicles=vehicles,
            chargers=chargers,
            routes=routes,
            price_windows=prices,
            horizon_start=start,
            horizon_days=7,
        )

        self.assertEqual(result.served_routes_count, 1)
        self.assertEqual(len(result.charge_sessions), 1)
        session = result.charge_sessions[0]
        self.assertEqual(session.start_at, datetime(2026, 4, 13, 10, 0))
        self.assertEqual(session.end_at, datetime(2026, 4, 13, 10, 30))
        self.assertAlmostEqual(session.energy_kwh, 30.0)
        self.assertAlmostEqual(session.target_soc_pct, 50.0)

    def test_marks_route_unserved_when_speed_is_impossible(self):
        start = datetime(2026, 4, 13, 8, 0)
        vehicles = [
            Vehicle(
                id=1,
                name="Mini Bus",
                battery_kwh=100,
                current_soc_pct=90,
                min_reserve_pct=20,
                efficiency_kwh_per_km=0.5,
                max_speed_kph=60,
                max_charge_power_kw=60,
                depot_id=1,
            )
        ]
        routes = [
            Route(
                id=1,
                name="Impossible Route",
                departure_at=datetime(2026, 4, 13, 9, 0),
                arrival_at=datetime(2026, 4, 13, 10, 0),
                distance_km=90,
                required_speed_kph=90,
                start_depot_id=1,
                end_depot_id=1,
            )
        ]

        result = run_weekly_plan(
            vehicles=vehicles,
            chargers=[],
            routes=routes,
            price_windows=[],
            horizon_start=start,
            horizon_days=7,
        )

        self.assertEqual(result.served_routes_count, 0)
        self.assertEqual(result.unserved_routes_count, 1)
        self.assertIn("speed", result.unserved_routes[0].reason)


if __name__ == "__main__":
    unittest.main()
