# MHPHackathon

GridPilot is a minimal Flask prototype for weekly EV route planning. Operators can add depots, chargers, electric vehicles, and planned routes, then let the app fetch weather and market data to generate a 7-day solar-aware charging and assignment plan.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open `http://127.0.0.1:5000`.

## What the MVP does

- Stores fleet, depot, charger, route, solar, and forecast data in SQLite
- Uses depot coordinates and solar panel metadata to estimate on-site generation from weather forecasts
- Combines market history and day-ahead price data to estimate buy prices automatically
- Generates a weekly plan in-process inside the Flask app
- Prioritizes route coverage, reserve safety, solar-first charging, lower grid charging cost, and avoiding unnecessary charging to 100%
- Shows route assignments, charging sessions, solar/grid split, and unserved routes with reasons
