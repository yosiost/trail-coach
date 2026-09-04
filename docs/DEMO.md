# Running a public demo

A demo instance lets people (recruiters, friends, curious runners) click through
Trail Coach with generic sample data — **no wiring required** — while your own
private instance stays untouched.

It's a **separate** service from your personal deployment, with its own database.

## What makes it a demo

| Setting | Value | Why |
|---------|-------|-----|
| `SEED_DEMO_DATA` | `1` | Seeds a generic example athlete, goal, and course ("Skyline 50K") on first boot, so there's no empty onboarding wall. |
| `AUTH_MODE` | `password` | Keeps a light gate so bots/crawlers can't hammer the LLM key. |
| `APP_PASSWORD` | `demo` (or anything you share) | A throwaway password you publish next to the demo link. |
| `DATABASE_URL` | its **own** empty Postgres | Never point a demo at your real database. |
| `LLM_API_KEY` | a **spend-capped / cheap-model** key | A public endpoint can attract usage — cap it. Consider a cheaper or free-tier model (e.g. Groq) for the demo. |

> ⚠️ **Cost & abuse:** a reachable instance that calls a paid LLM can accrue
> usage. Use a key with a hard spend cap (or a free-tier provider), keep the
> password gate on, and prefer a low-cost model. `AUTH_MODE=none` (fully open) is
> **not recommended** for a public demo.

## Deploy it (Render Blueprint)

1. In Render, **New → Blueprint**, point it at this repo. It reads
   [`render.yaml`](../render.yaml) and creates a web service + a Postgres DB.
2. Name the service something like `trail-coach-demo`.
3. Set the dashboard secrets:
   - `APP_PASSWORD` = `demo`
   - `LLM_API_KEY` = your spend-capped key
   - `SEED_DEMO_DATA` = `1`
4. Deploy. Open the URL, sign in with `demo`, and you're in a fully populated demo.

## Share it

Put the link and password together, e.g.:

> **Live demo:** https://trail-coach-demo.onrender.com — password `demo`
> _(free tier — first load may take ~30s to wake)_

## Resetting the demo

To wipe accumulated demo activity (chats, edits) and reseed, drop/recreate the
demo database, or clear its tables — the next boot reseeds from `SEED_DEMO_DATA`.
