# Examples

Generic, non-personal sample data you can copy and adapt for your own race. Nothing
here is tied to any specific athlete — replace the values with yours.

## Contents

- `plans/generic_16wk_50k.csv` — a sample training plan showing the CSV format the app
  imports. Columns:
  `Week, Date, Day, Phase, Session, Distance_km, Vert_m, Duration_min, HR_Zone, RPE, Notes`.
  Dates are Sundays-first weeks; adjust to your own week-start preference.
- `courses/generic_50k.json` — a sample course profile. This schema is the single source
  of truth for both the Course view and the AI coach. `img` fields are left empty (per-segment
  photos are optional); fill them with filenames if you add course images.

## How to use

Until the in-app onboarding flow lands, point the app at your own plan/course (see the
project README for the configured paths / env vars), or copy one of these files and edit it.
