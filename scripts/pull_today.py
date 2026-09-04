"""Pull today's Garmin activities with full detail and laps."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from datetime import date
from api.garmin import connect, hr_zone

client = connect()
today = date.today().isoformat()

print(f"Fetching activities for {today}...\n")
acts = client.get_activities_by_date(today, today, activitytype="")

if not acts:
    print("No activities found.")
    sys.exit(0)

for a in acts:
    avg_hr = int(a.get("averageHR") or 0)
    print(f"  Name     : {a.get('activityName')}")
    print(f"  Type     : {a.get('activityType', {}).get('typeKey')}")
    print(f"  Distance : {round((a.get('distance') or 0)/1000, 2)} km")
    print(f"  Duration : {round((a.get('duration') or 0)/60, 1)} min")
    print(f"  Elev gain: {int(a.get('elevationGain') or 0)} m")
    print(f"  Avg HR   : {avg_hr} bpm ({hr_zone(avg_hr) if avg_hr else '—'})")
    print(f"  Max HR   : {int(a.get('maxHR') or 0)} bpm")
    print(f"  Calories : {a.get('calories')}")

    activity_id = a.get("activityId")
    if activity_id:
        try:
            details = client.get_activity_splits(activity_id)
            laps = details.get("lapDTOs", [])
            if laps:
                print(f"\n  Laps ({len(laps)}):")
                for i, lap in enumerate(laps, 1):
                    lap_hr   = int(lap.get("averageHR") or 0)
                    lap_dist = round((lap.get("distance") or 0) / 1000, 2)
                    lap_dur  = round((lap.get("duration") or 0) / 60, 1)
                    lap_elev = int(lap.get("elevationGain") or 0)
                    zone     = hr_zone(lap_hr) if lap_hr else "—"
                    print(f"    {i:2}. {lap_dist:5.2f}km  {lap_dur:5.1f}min  HR:{lap_hr:3} ({zone})  ↑{lap_elev}m")
        except Exception as e:
            print(f"  (could not fetch laps: {e})")
    print()
