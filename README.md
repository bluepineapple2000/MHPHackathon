# MHPHackathon

GridPilot is a minimal Flask prototype for weekly EV route planning. Operators can add depots, chargers, electric vehicles, planned routes, and electricity price windows, then generate a 7-day assignment and charging plan.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open `http://127.0.0.1:5000`.

## What the MVP does

- Stores fleet, depot, charger, route, and price data in SQLite
- Generates a weekly plan in-process inside the Flask app
- Prioritizes route coverage, reserve safety, lower charging cost, and avoiding unnecessary charging to 100%
- Shows route assignments, charging sessions, and unserved routes with reasons
