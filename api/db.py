"""Persistent storage — Postgres (Neon) if DATABASE_URL is set, SQLite otherwise."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_PG = bool(DATABASE_URL)

_DB = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent)) / "trail_coach.db"

# Parameter placeholder differs between backends
P = "%s" if _PG else "?"
_JSON_LEN = "json_array_length(messages::json)" if _PG else "json_array_length(messages)"


@contextmanager
def _conn():
    if _PG:
        import psycopg2
        c = psycopg2.connect(DATABASE_URL)
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    else:
        c = sqlite3.connect(str(_DB))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _fetchall(conn, sql: str, params: tuple = ()) -> list[dict]:
    if _PG:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _fetchone(conn, sql: str, params: tuple = ()) -> dict | None:
    if _PG:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            r = cur.fetchone()
            return dict(r) if r else None
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def _run(conn, sql: str, params: tuple = ()) -> None:
    if _PG:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        conn.execute(sql, params)


# ── Time helpers ─────────────────────────────────────────────────────────────

def hms_to_sec(hms: str) -> int:
    """Parse 'H:MM' or 'H:MM:SS' into total seconds."""
    parts = [int(p) for p in hms.strip().split(":")]
    if len(parts) == 2:
        h, m, s = parts[0], parts[1], 0
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError(f"Invalid time format: {hms!r} (expected H:MM or H:MM:SS)")
    return h * 3600 + m * 60 + s


def sec_to_hms(sec: int) -> str:
    """Format total seconds as 'H:MM:SS'."""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as conn:
        if _PG:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS plan_overrides (
                    race_id INTEGER NOT NULL DEFAULT 0,
                    date TEXT NOT NULL, field TEXT NOT NULL,
                    value TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (race_id, date, field)
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                    messages TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    persona TEXT NOT NULL DEFAULT 'coach',
                    race_id INTEGER
                )""")
                cur.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS persona TEXT NOT NULL DEFAULT 'coach'"
                )
                cur.execute("""
                CREATE TABLE IF NOT EXISTS coach_notes (
                    id SERIAL PRIMARY KEY,
                    content TEXT NOT NULL, created_at TEXT NOT NULL,
                    race_id INTEGER
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS athlete_references (
                    id SERIAL PRIMARY KEY,
                    race_id INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL, name TEXT NOT NULL,
                    content TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE (race_id, category, name)
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id SERIAL PRIMARY KEY,
                    race_name TEXT NOT NULL, race_date TEXT NOT NULL,
                    distance_km REAL NOT NULL, vert_m INTEGER NOT NULL,
                    aspirational_time_sec INTEGER NOT NULL,
                    realistic_min_sec INTEGER NOT NULL, realistic_max_sec INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    goal_id INTEGER NOT NULL REFERENCES goals(id),
                    predicted_at TEXT NOT NULL,
                    predicted_time_sec INTEGER NOT NULL,
                    confidence_low_sec INTEGER NOT NULL, confidence_high_sec INTEGER NOT NULL,
                    verdict TEXT NOT NULL, reasoning TEXT NOT NULL,
                    signals_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS race_fuel_segments (
                    race_id INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL,
                    seg TEXT NOT NULL, dur_min INTEGER NOT NULL,
                    food TEXT NOT NULL, carbs INTEGER NOT NULL,
                    crux INTEGER NOT NULL DEFAULT 0, flag TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (race_id, position)
                )""")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS config_blobs (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
        else:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS plan_overrides (
                race_id INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL, field TEXT NOT NULL,
                value TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (race_id, date, field)
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                persona TEXT NOT NULL DEFAULT 'coach',
                race_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS coach_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL, created_at TEXT NOT NULL,
                race_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS athlete_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL, name TEXT NOT NULL,
                content TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (race_id, category, name)
            );
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_name TEXT NOT NULL, race_date TEXT NOT NULL,
                distance_km REAL NOT NULL, vert_m INTEGER NOT NULL,
                aspirational_time_sec INTEGER NOT NULL,
                realistic_min_sec INTEGER NOT NULL, realistic_max_sec INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL REFERENCES goals(id),
                predicted_at TEXT NOT NULL,
                predicted_time_sec INTEGER NOT NULL,
                confidence_low_sec INTEGER NOT NULL, confidence_high_sec INTEGER NOT NULL,
                verdict TEXT NOT NULL, reasoning TEXT NOT NULL,
                signals_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS race_fuel_segments (
                race_id INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                seg TEXT NOT NULL, dur_min INTEGER NOT NULL,
                food TEXT NOT NULL, carbs INTEGER NOT NULL,
                crux INTEGER NOT NULL DEFAULT 0, flag TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (race_id, position)
            );
            CREATE TABLE IF NOT EXISTS config_blobs (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
            try:
                conn.execute("ALTER TABLE chat_sessions ADD COLUMN persona TEXT NOT NULL DEFAULT 'coach'")
            except sqlite3.OperationalError:
                pass  # column already exists
        _migrate_multi_race(conn)


# ── Multi-race migration ──────────────────────────────────────────────────────
# Race-scoped data hangs off a race_id (the goals.id of the race it belongs to).
# For athlete_references the sentinel race_id 0 means "shared / athlete-level"
# (physiology, zones, foods, training locations) — visible under every race; a
# non-zero race_id marks race-specific rows (course_profile, aid_stations). The
# other race-scoped tables always carry the owning race's id.

# (category, name) reference rows that belong to a race rather than the athlete.
_RACE_SCOPED_REFS = {("trails", "course_profile"), ("fueling", "aid_stations")}
_SHARED_RACE_ID = 0  # athlete_references sentinel for athlete-level (shared) rows

# config_blobs keys that are race-scoped; stored under "<key>:<race_id>".
_RACE_CONFIG_KEYS = ("course_json", "plan_csv")


def _table_columns(conn, table: str) -> set[str]:
    if _PG:
        rows = _fetchall(
            conn,
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {r["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _backfill_race_id(conn) -> int | None:
    """The race id to attach to pre-existing (single-race) data: the current
    active goal, else the most recent goal. None on an empty/fresh install."""
    row = _fetchone(
        conn,
        f"SELECT id FROM goals WHERE status = {P} ORDER BY created_at DESC LIMIT 1",
        ("active",),
    )
    if not row:
        row = _fetchone(conn, "SELECT id FROM goals ORDER BY created_at DESC LIMIT 1")
    return row["id"] if row else None


def _migrate_multi_race(conn) -> None:
    """Idempotent, per-table: give already-seeded single-race DBs a race_id and
    point active_race_id at the existing race. New installs are created with the
    final schema, so each guard sees race_id already present and no-ops."""
    r0 = _backfill_race_id(conn)

    # chat_sessions / coach_notes: a plain column add + backfill (PK unchanged).
    for table in ("chat_sessions", "coach_notes"):
        if "race_id" not in _table_columns(conn, table):
            _run(conn, f"ALTER TABLE {table} ADD COLUMN race_id INTEGER")
            if r0 is not None:
                _run(conn, f"UPDATE {table} SET race_id = {P} WHERE race_id IS NULL", (r0,))

    # plan_overrides / race_fuel_segments / athlete_references: the PK/UNIQUE key
    # changes, so rebuild on SQLite and ALTER the constraint on Postgres.
    if "race_id" not in _table_columns(conn, "plan_overrides"):
        _migrate_table_race_id(
            conn, "plan_overrides",
            cols="date TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL",
            copy_cols="date, field, value, updated_at",
            key="PRIMARY KEY (race_id, date, field)",
            pg_key="ADD PRIMARY KEY (race_id, date, field)",
            backfill=r0 or _SHARED_RACE_ID,
        )
    if "race_id" not in _table_columns(conn, "race_fuel_segments"):
        _migrate_table_race_id(
            conn, "race_fuel_segments",
            cols=("position INTEGER NOT NULL, seg TEXT NOT NULL, dur_min INTEGER NOT NULL, "
                  "food TEXT NOT NULL, carbs INTEGER NOT NULL, crux INTEGER NOT NULL DEFAULT 0, "
                  "flag TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL"),
            copy_cols="position, seg, dur_min, food, carbs, crux, flag, updated_at",
            key="PRIMARY KEY (race_id, position)",
            pg_key="ADD PRIMARY KEY (race_id, position)",
            backfill=r0 or _SHARED_RACE_ID,
        )
    if "race_id" not in _table_columns(conn, "athlete_references"):
        # Shared (athlete-level) rows get race_id 0; course/aid facts get r0.
        self_id = "id INTEGER PRIMARY KEY AUTOINCREMENT, " if not _PG else "id SERIAL PRIMARY KEY, "
        _migrate_table_race_id(
            conn, "athlete_references",
            cols=self_id + "category TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT NOT NULL",
            copy_cols="category, name, content, updated_at",
            key="UNIQUE (race_id, category, name)",
            pg_key="ADD UNIQUE (race_id, category, name)",
            backfill=_SHARED_RACE_ID,
            has_own_pk=True,
            pg_drop="athlete_references_category_name_key",
        )
        if r0 is not None:
            for category, name in _RACE_SCOPED_REFS:
                _run(
                    conn,
                    f"UPDATE athlete_references SET race_id = {P} WHERE category = {P} AND name = {P}",
                    (r0, category, name),
                )

    # config_blobs: move the single course_json / plan_csv into race-scoped keys.
    if r0 is not None:
        for key in _RACE_CONFIG_KEYS:
            scoped = f"{key}:{r0}"
            if _fetchone(conn, f"SELECT 1 FROM config_blobs WHERE key = {P}", (scoped,)):
                continue  # already migrated
            old = _fetchone(conn, f"SELECT value, updated_at FROM config_blobs WHERE key = {P}", (key,))
            if old:
                _run(
                    conn,
                    f"INSERT INTO config_blobs (key, value, updated_at) VALUES ({P},{P},{P}) "
                    f"ON CONFLICT (key) DO NOTHING",
                    (scoped, old["value"], old["updated_at"]),
                )

    # Point the active-race pointer at the existing race (once).
    if r0 is not None and not _fetchone(conn, f"SELECT 1 FROM config_blobs WHERE key = {P}", ("active_race_id",)):
        now = datetime.now(timezone.utc).isoformat()
        _run(
            conn,
            f"INSERT INTO config_blobs (key, value, updated_at) VALUES ({P},{P},{P}) "
            f"ON CONFLICT (key) DO NOTHING",
            ("active_race_id", str(r0), now),
        )


def _migrate_table_race_id(
    conn, table: str, cols: str, copy_cols: str, key: str, pg_key: str,
    backfill: int, has_own_pk: bool = False, pg_drop: str | None = None,
) -> None:
    """Add race_id + a new PK/UNIQUE key to a table with pre-existing rows.
    Postgres ALTERs in place; SQLite rebuilds (it can't alter a PK)."""
    if _PG:
        _run(conn, f"ALTER TABLE {table} ADD COLUMN race_id INTEGER NOT NULL DEFAULT {backfill}")
        if pg_drop:
            _run(conn, f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {pg_drop}")
        if not has_own_pk:
            _run(conn, f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
        _run(conn, f"ALTER TABLE {table} {pg_key}")
        return
    # SQLite rebuild: new table → copy rows (stamping race_id) → swap names.
    race_col = "race_id INTEGER NOT NULL DEFAULT 0, "
    _run(conn, f"CREATE TABLE {table}__new ({race_col}{cols}, {key})")
    _run(
        conn,
        f"INSERT INTO {table}__new (race_id, {copy_cols}) "
        f"SELECT {backfill}, {copy_cols} FROM {table}",
    )
    _run(conn, f"DROP TABLE {table}")
    _run(conn, f"ALTER TABLE {table}__new RENAME TO {table}")


# ── Config blobs (key/value: plan CSV, course JSON, onboarding flags) ─────────

def get_config_blob(key: str) -> str | None:
    with _conn() as conn:
        row = _fetchone(conn, f"SELECT value FROM config_blobs WHERE key = {P}", (key,))
    return row["value"] if row else None


def set_config_blob(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"INSERT INTO config_blobs (key, value, updated_at) "
        f"VALUES ({P},{P},{P}) "
        f"ON CONFLICT (key) DO UPDATE SET "
        f"value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"
    )
    with _conn() as conn:
        _run(conn, sql, (key, value, now))


# ── Active race pointer & race-scoped config blobs ────────────────────────────
# The "race" is the goals row; active_race_id names the one currently in view.

def get_active_race_id() -> int | None:
    """The race currently in view. Falls back to the latest active goal so an
    un-migrated or mid-onboarding DB still resolves a race."""
    raw = get_config_blob("active_race_id")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    g = get_active_goal()
    return g["id"] if g else None


def set_active_race_id(race_id: int) -> None:
    set_config_blob("active_race_id", str(int(race_id)))


def _race_config_key(key: str, race_id: int | None) -> str | None:
    rid = race_id if race_id is not None else get_active_race_id()
    return f"{key}:{rid}" if rid is not None else None


def get_race_config_blob(key: str, race_id: int | None = None) -> str | None:
    """Read a race-scoped config blob (course_json / plan_csv) for the given or
    active race. Returns None when no race is resolvable."""
    scoped = _race_config_key(key, race_id)
    return get_config_blob(scoped) if scoped else None


def set_race_config_blob(key: str, value: str, race_id: int | None = None) -> None:
    scoped = _race_config_key(key, race_id)
    if scoped is None:
        raise ValueError(f"Cannot set race-scoped blob {key!r}: no active race.")
    set_config_blob(scoped, value)


def is_onboarded() -> bool:
    return (get_config_blob("onboarded") or "").strip().lower() == "true"


def heal_onboarded_flag() -> None:
    """Back-compat: an already-configured install (has an active goal) predates
    the onboarding flag — mark it onboarded so it never shows the setup wizard."""
    if is_onboarded():
        return
    if get_active_goal():
        set_config_blob("onboarded", "true")


# ── Plan overrides ────────────────────────────────────────────────────────────

def get_plan_overrides(race_id: int | None = None) -> dict[str, dict[str, str]]:
    rid = race_id if race_id is not None else get_active_race_id()
    with _conn() as conn:
        rows = _fetchall(
            conn, f"SELECT date, field, value FROM plan_overrides WHERE race_id = {P}", (rid,)
        )
    result: dict[str, dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["date"], {})[r["field"]] = r["value"]
    return result


def set_plan_overrides(updates: list[dict], race_id: int | None = None) -> str:
    rid = race_id if race_id is not None else get_active_race_id()
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"INSERT INTO plan_overrides (race_id, date, field, value, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P}) "
        f"ON CONFLICT (race_id, date, field) DO UPDATE SET "
        f"value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"
    )
    with _conn() as conn:
        for u in updates:
            _run(conn, sql, (rid, u["date"], u["field"], u["value"], now))
    applied = [f"{u['date']}: {u['field']}={u['value']}" for u in updates]
    return "Updated:\n" + "\n".join(applied)


# ── Chat sessions ─────────────────────────────────────────────────────────────

def list_sessions(race_id: int | None = None) -> list[dict]:
    """Sessions for the given or active race. Legacy rows with a NULL race_id
    surface under every race so nothing is orphaned by the migration."""
    rid = race_id if race_id is not None else get_active_race_id()
    sql = (
        f"SELECT id, title, created_at, updated_at, persona, "
        f"{_JSON_LEN} AS message_count "
        f"FROM chat_sessions WHERE race_id = {P} OR race_id IS NULL ORDER BY updated_at DESC"
    )
    with _conn() as conn:
        return _fetchall(conn, sql, (rid,))


def get_session(session_id: str) -> dict | None:
    sql = f"SELECT id, title, messages, created_at, updated_at, persona FROM chat_sessions WHERE id = {P}"
    with _conn() as conn:
        row = _fetchone(conn, sql, (session_id,))
    if not row:
        return None
    return {**row, "messages": json.loads(row["messages"])}


def upsert_session(
    session_id: str, title: str, messages: list, created_at: str, updated_at: str,
    persona: str = "coach", race_id: int | None = None,
) -> None:
    rid = race_id if race_id is not None else get_active_race_id()
    sql = (
        f"INSERT INTO chat_sessions (id, title, messages, created_at, updated_at, persona, race_id) "
        f"VALUES ({P},{P},{P},{P},{P},{P},{P}) "
        f"ON CONFLICT (id) DO UPDATE SET "
        f"title = EXCLUDED.title, messages = EXCLUDED.messages, updated_at = EXCLUDED.updated_at, "
        f"persona = EXCLUDED.persona"
    )
    with _conn() as conn:
        _run(conn, sql, (session_id, title, json.dumps(messages), created_at, updated_at, persona, rid))


def delete_session(session_id: str) -> None:
    with _conn() as conn:
        _run(conn, f"DELETE FROM chat_sessions WHERE id = {P}", (session_id,))


# ── Coach notes ───────────────────────────────────────────────────────────────

def get_coach_notes(limit: int = 8, race_id: int | None = None) -> list[dict]:
    rid = race_id if race_id is not None else get_active_race_id()
    sql = (
        f"SELECT id, content, created_at FROM coach_notes "
        f"WHERE race_id = {P} OR race_id IS NULL ORDER BY created_at DESC LIMIT {P}"
    )
    with _conn() as conn:
        return _fetchall(conn, sql, (rid, limit))


def add_coach_note(content: str, race_id: int | None = None) -> int:
    rid = race_id if race_id is not None else get_active_race_id()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        if _PG:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO coach_notes (content, created_at, race_id) VALUES ({P},{P},{P}) RETURNING id",
                    (content, now, rid),
                )
                return cur.fetchone()[0]
        cur = conn.execute(
            f"INSERT INTO coach_notes (content, created_at, race_id) VALUES ({P},{P},{P})",
            (content, now, rid),
        )
        return cur.lastrowid


# ── Athlete references ────────────────────────────────────────────────────────

# Generic example seed data (demo only — runs under SEED_DEMO_DATA). A real
# install starts empty and writes its own via onboarding. Aligned to the
# examples/ Skyline 50K so demo mode is coherent.
_DEFAULT_REFERENCES: list[tuple[str, str, str]] = [
    ("athlete_profile", "basics",
     "Name: Alex Example. Weight: ~70kg. Height: ~175cm. Max HR: 185 bpm. "
     "Running background: recreational road runner moving up to trail ultras. "
     "Target race: Skyline 50K (example) — 50km / ~2400m↑. "
     "Replace this with your own details during onboarding."),

    ("athlete_profile", "medical_notes",
     "No known injuries. Watch for quad load on long descents — train eccentric strength. "
     "If using a wrist optical HR sensor, see assumptions/optical_hr_unreliable."),

    ("fueling", "staple_fuel",
     "Choose a reliable staple carb source you tolerate (gels, chews, dates, or real food) and know its carbs "
     "per unit — e.g. a typical gel ≈ 22–25g carbs. Build race fueling around your staple plus aid-station food."),

    ("fueling", "carb_target",
     "Target fueling rate: 60–90g carbs/hr during races and sustained long efforts (>90min), scaled to duration "
     "and tolerance. This is a trainable adaptation — practice carb intake on every long run."),

    ("fueling", "pre_run",
     "Pre-run fueling protocol: TBD — record your confirmed routine as training progresses."),

    ("fueling", "aid_stations",
     "Skyline 50K (example) aid stations with food: Ridgeline Aid (~km12), High Col (~km24), "
     "Lakeside Aid (~km38). Carry enough between stations. Replace with your race's aid stations."),

    ("trails", "training_locations",
     "Record your regular training locations here — e.g. a local hill loop for weekday vert reps, a longer "
     "trail for weekend long runs, and a treadmill/incline option for climb simulation — with typical "
     "distance and vert for each."),

    ("trails", "course_profile",
     "Skyline 50K (example) course: Valley Start → Ridgeline → High Col → Forest Descent → Lakeside → Finish "
     "(50km / ~2400m↑). Key sections: the sustained climb to High Col (course high point ~1850m) is the crux; "
     "the long Forest Descent to Lakeside is a quad-killer — protect the legs. "
     "Aid/checkpoints: Ridgeline Aid, High Col, Lakeside Aid, Finish. "
     "This is example data — replace it with your race course during onboarding."),

    ("assumptions", "optical_hr_unreliable",
     "Wrist optical HR sensors are often unreliable during intervals and high-intensity efforts — they can "
     "spike, lag 2–3 minutes into hard reps, or flatline mid-session. Use RPE as the PRIMARY intensity measure "
     "for quality sessions; treat lap HR during hard intervals as informational, not diagnostic."),

    ("assumptions", "analyze_by_lap",
     "Always analyze workouts using lap-by-lap data (via get_activity_laps), never activity averages. "
     "Overall averages include warmup, recovery jogs, and cooldown — they dilute the actual quality work. "
     "A VO2max session with 5 hard reps should be evaluated rep-by-rep: distance, time, and RPE per lap."),

    ("assumptions", "week_structure",
     "Training week runs Monday–Sunday by default, with long runs typically on the weekend. "
     "Adjust to your own schedule and locale."),

    ("assumptions", "hr_zones",
     "Calibrated HR zones (max HR 185 bpm): "
     "Z1: <133 bpm | Z2: 133–152 bpm | Z3: 153–161 bpm | Z4: 162–170 bpm | Z5: 171–185 bpm. "
     "~80% of weekly volume targets Z1–Z2. VO2max intervals target Z5 (verify by RPE, not HR — see "
     "optical_hr_unreliable). Zones are derived from max HR and update when you set yours in onboarding."),

    ("nutrition_protocol", "carb_intake_rate",
     "Target carb intake during exercise: up to ~90g/hr for efforts >2.5h using multiple-transportable carbs "
     "(glucose:fructose ~2:1) since the SGLT1 transporter saturates ~60g/hr alone. "
     "60g/hr is sufficient for 1–2.5h efforts. Gut tolerance is trainable — "
     "practice race-rate fueling on every long run >90min, ramping gradually if GI distress occurs."),

    ("nutrition_protocol", "hydration_sodium",
     "Hydration target: replace ~70–80% of sweat losses during exercise (avoid overdrinking — hyponatremia risk). "
     "Estimate sweat rate via pre/post-run weight delta (1kg lost ≈ 1L sweat). "
     "Sodium target: 300–700mg/hr during hot/long efforts, scaled to individual sweat/salt rate. "
     "TBD: measure your own sweat rate and sodium concentration."),

    ("nutrition_protocol", "race_day_plan",
     "Race-day nutrition plan (draft — confirm and refine with long-run practice): "
     "target your carbs/hr rate for the full effort using your staple fuel plus aid-station food "
     "(Ridgeline, High Col, Lakeside for the Skyline 50K example). "
     "Sodium and pre-race meal timing TBD — log specifics here once confirmed. Replace during onboarding."),
]


# Course-geography references are corrected in place on every init so existing
# databases pick up route fixes instead of keeping a stale seeded row.
_ALWAYS_REFRESH_REFERENCES = {
    ("trails", "course_profile"),
    ("fueling", "aid_stations"),
}


def _ref_race_id(category: str, name: str, race_id: int | None) -> int:
    """Which race_id a reference row lives under: the active race for race-scoped
    facts (course_profile, aid_stations), else the shared sentinel."""
    if (category, name) not in _RACE_SCOPED_REFS:
        return _SHARED_RACE_ID
    rid = race_id if race_id is not None else get_active_race_id()
    return rid if rid is not None else _SHARED_RACE_ID


def init_athlete_references() -> None:
    """Seed default athlete references, force-refreshing corrected course refs."""
    now = datetime.now(timezone.utc).isoformat()
    active = get_active_race_id()
    seed_sql = (
        f"INSERT INTO athlete_references (race_id, category, name, content, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P}) "
        f"ON CONFLICT (race_id, category, name) DO NOTHING"
    )
    refresh_sql = (
        f"INSERT INTO athlete_references (race_id, category, name, content, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P}) "
        f"ON CONFLICT (race_id, category, name) DO UPDATE SET "
        f"content = EXCLUDED.content, updated_at = EXCLUDED.updated_at"
    )
    with _conn() as conn:
        for category, name, content in _DEFAULT_REFERENCES:
            rid = _ref_race_id(category, name, active)
            sql = refresh_sql if (category, name) in _ALWAYS_REFRESH_REFERENCES else seed_sql
            _run(conn, sql, (rid, category, name, content, now))


def get_athlete_references(
    category: str | None = None, limit: int | None = None, race_id: int | None = None
) -> list[dict]:
    """Returns shared (athlete-level) rows plus the active race's race-specific
    rows (course_profile, aid_stations). Without `limit`: full set, alphabetical
    (used by /api/references and the get_athlete_references chat tool). With
    `limit`: most-recently-updated first, capped (build_context() injection)."""
    rid = race_id if race_id is not None else get_active_race_id()
    race_clause = f"race_id IN ({P},{P})"
    race_params = (_SHARED_RACE_ID, rid)
    cols = "SELECT category, name, content, updated_at FROM athlete_references"
    if limit:
        if category:
            sql = f"{cols} WHERE category = {P} AND {race_clause} ORDER BY updated_at DESC LIMIT {P}"
            params: tuple = (category, *race_params, limit)
        else:
            sql = f"{cols} WHERE {race_clause} ORDER BY updated_at DESC LIMIT {P}"
            params = (*race_params, limit)
    elif category:
        sql = f"{cols} WHERE category = {P} AND {race_clause} ORDER BY name"
        params = (category, *race_params)
    else:
        sql = f"{cols} WHERE {race_clause} ORDER BY category, name"
        params = race_params
    with _conn() as conn:
        return _fetchall(conn, sql, params)


def upsert_athlete_reference(category: str, name: str, content: str, race_id: int | None = None) -> None:
    rid = _ref_race_id(category, name, race_id)
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"INSERT INTO athlete_references (race_id, category, name, content, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P}) "
        f"ON CONFLICT (race_id, category, name) DO UPDATE SET "
        f"content = EXCLUDED.content, updated_at = EXCLUDED.updated_at"
    )
    with _conn() as conn:
        _run(conn, sql, (rid, category, name, content, now))


# ── Race fuel plan ────────────────────────────────────────────────────────────
# Segments in course order. dur_min drives the derived g/hr rate. Seeded once;
# the dietitian edits it via chat (set_race_fuel) and the athlete-facing Fueling
# panel reads it from /api/race/fuel.
# Generic example fuel plan (demo only), aligned to the Skyline 50K example.
_DEFAULT_FUEL_SEGMENTS: list[dict] = [
    {"seg": "Start → Ridgeline",     "dur_min": 90,  "food": "2 gels + chews",                "carbs": 90,  "crux": 0, "flag": ""},
    {"seg": "Ridgeline → High Col",  "dur_min": 120, "food": "3 gels + bar + electrolyte",    "carbs": 130, "crux": 1, "flag": "⚠️"},
    {"seg": "High Col → Lakeside",   "dur_min": 110, "food": "3 gels + chews",                "carbs": 120, "crux": 0, "flag": ""},
    {"seg": "Lakeside → Finish",     "dur_min": 70,  "food": "2 gels + 1 caffeine gel + cola", "carbs": 80, "crux": 0, "flag": ""},
]


def init_race_fuel() -> None:
    """Seed the default fuel plan once for the active race, if it has none yet."""
    now = datetime.now(timezone.utc).isoformat()
    rid = get_active_race_id() or _SHARED_RACE_ID
    with _conn() as conn:
        existing = _fetchone(
            conn, f"SELECT COUNT(*) AS n FROM race_fuel_segments WHERE race_id = {P}", (rid,)
        )
        if existing and existing["n"]:
            return
        sql = (
            f"INSERT INTO race_fuel_segments "
            f"(race_id, position, seg, dur_min, food, carbs, crux, flag, updated_at) "
            f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P})"
        )
        for i, s in enumerate(_DEFAULT_FUEL_SEGMENTS):
            _run(conn, sql, (rid, i, s["seg"], s["dur_min"], s["food"], s["carbs"], s["crux"], s["flag"], now))


def get_race_fuel(race_id: int | None = None) -> list[dict]:
    rid = race_id if race_id is not None else get_active_race_id()
    with _conn() as conn:
        rows = _fetchall(
            conn,
            f"SELECT position, seg, dur_min, food, carbs, crux, flag "
            f"FROM race_fuel_segments WHERE race_id = {P} ORDER BY position",
            (rid,),
        )
    return [{**r, "crux": bool(r["crux"])} for r in rows]


def set_race_fuel(segments: list[dict], race_id: int | None = None) -> int:
    """Replace the active race's fuel plan with the given ordered segments."""
    if not segments:
        raise ValueError("At least one fuel segment is required.")
    rid = race_id if race_id is not None else get_active_race_id()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, s in enumerate(segments):
        seg = str(s.get("seg", "")).strip()
        food = str(s.get("food", "")).strip()
        if not seg or not food:
            raise ValueError("Each segment needs a 'seg' label and a 'food' description.")
        dur_min = int(s["dur_min"])
        carbs = int(s["carbs"])
        if dur_min <= 0:
            raise ValueError(f"Segment {seg!r} has non-positive dur_min.")
        crux = 1 if s.get("crux") else 0
        flag = str(s.get("flag") or "").strip()
        rows.append((rid, i, seg, dur_min, food, carbs, crux, flag, now))
    sql = (
        f"INSERT INTO race_fuel_segments "
        f"(race_id, position, seg, dur_min, food, carbs, crux, flag, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P})"
    )
    with _conn() as conn:
        _run(conn, f"DELETE FROM race_fuel_segments WHERE race_id = {P}", (rid,))
        for r in rows:
            _run(conn, sql, r)
    return len(rows)


# ── Goals & predictions ───────────────────────────────────────────────────────

_DEFAULT_GOAL: dict = {
    "race_name": "Skyline 50K (example)",
    "race_date": "2025-06-14",
    "distance_km": 50,
    "vert_m": 2400,
    "aspirational_time_sec": hms_to_sec("6:30:00"),
    "realistic_min_sec": hms_to_sec("6:15:00"),
    "realistic_max_sec": hms_to_sec("7:00:00"),
    "notes": "Example demo goal — replace with your own race during onboarding.",
}


def init_goals() -> None:
    """Seed the default active goal iff no active goal exists yet."""
    with _conn() as conn:
        existing = _fetchone(conn, f"SELECT id FROM goals WHERE status = {P} LIMIT 1", ("active",))
        if existing:
            return
        now = datetime.now(timezone.utc).isoformat()
        g = _DEFAULT_GOAL
        # Seed a rolling FUTURE race date (~16 weeks out) so a fresh demo has a
        # race you can actually train toward — plan generation and "This Week"
        # need a race in the future.
        race_date = (datetime.now(timezone.utc).date() + timedelta(weeks=16)).isoformat()
        sql = (
            f"INSERT INTO goals (race_name, race_date, distance_km, vert_m, "
            f"aspirational_time_sec, realistic_min_sec, realistic_max_sec, "
            f"status, notes, created_at, updated_at) "
            f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})"
        )
        _run(conn, sql, (
            g["race_name"], race_date, g["distance_km"], g["vert_m"],
            g["aspirational_time_sec"], g["realistic_min_sec"], g["realistic_max_sec"],
            "active", g["notes"], now, now,
        ))


# Bump whenever the objective course facts in _DEFAULT_REFERENCES / _DEFAULT_GOAL
# change. Databases seed their references and goal exactly once, so without this
# an already-seeded DB (e.g. production Postgres) keeps the first-seeded course
# forever. sync_course_data() re-applies these facts once per version, leaving
# athlete-accumulated references (sweat rate, confirmed foods, etc.) untouched.
_COURSE_DATA_VERSION = "generic-1"

# References that are objective, app-owned race facts — safe to overwrite on a
# version bump. Athlete-refined entries (hydration_sodium, pre_run, …) are excluded.
_COURSE_FACT_REFS = {"basics", "medical_notes", "aid_stations", "course_profile", "race_day_plan"}


def sync_course_data() -> None:
    """Idempotent, version-gated: push updated course facts into already-seeded DBs."""
    now = datetime.now(timezone.utc).isoformat()
    active = get_active_race_id()
    upsert = (
        f"INSERT INTO athlete_references (race_id, category, name, content, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P}) "
        f"ON CONFLICT (race_id, category, name) DO UPDATE SET "
        f"content = EXCLUDED.content, updated_at = EXCLUDED.updated_at"
    )
    with _conn() as conn:
        marker = _fetchone(
            conn,
            f"SELECT content FROM athlete_references WHERE category = {P} AND name = {P}",
            ("_meta", "course_data_version"),
        )
        if marker and marker.get("content") == _COURSE_DATA_VERSION:
            return
        for category, name, content in _DEFAULT_REFERENCES:
            if name in _COURSE_FACT_REFS:
                _run(conn, upsert, (_ref_race_id(category, name, active), category, name, content, now))
        # distance/vert are race facts, not athlete-set targets — safe to sync.
        goal = _fetchone(
            conn, f"SELECT id FROM goals WHERE status = {P} ORDER BY created_at DESC LIMIT 1", ("active",)
        )
        if goal:
            _run(
                conn,
                f"UPDATE goals SET distance_km = {P}, vert_m = {P}, updated_at = {P} WHERE id = {P}",
                (_DEFAULT_GOAL["distance_km"], _DEFAULT_GOAL["vert_m"], now, goal["id"]),
            )
        _run(conn, upsert, (_SHARED_RACE_ID, "_meta", "course_data_version", _COURSE_DATA_VERSION, now))


def get_active_goal() -> dict | None:
    """The goal for the race in view (active_race_id), else the latest active
    goal. Reads the pointer blob directly to avoid recursing via
    get_active_race_id()."""
    raw = get_config_blob("active_race_id")
    with _conn() as conn:
        if raw:
            try:
                g = _fetchone(conn, f"SELECT * FROM goals WHERE id = {P}", (int(raw),))
            except ValueError:
                g = None
            if g:
                return g
        return _fetchone(
            conn, f"SELECT * FROM goals WHERE status = {P} ORDER BY created_at DESC LIMIT 1", ("active",)
        )


def get_goal_by_id(goal_id: int) -> dict | None:
    with _conn() as conn:
        return _fetchone(conn, f"SELECT * FROM goals WHERE id = {P}", (goal_id,))


def list_races() -> list[dict]:
    """Every race (goal), newest first, each flagged with whether it's the one
    currently in view (active_race_id)."""
    active = get_active_race_id()
    with _conn() as conn:
        rows = _fetchall(conn, "SELECT * FROM goals ORDER BY created_at DESC")
    for r in rows:
        r["is_active"] = r["id"] == active
    return rows


def copy_race_data(src_race_id: int, dst_race_id: int, *, fuel: bool = False, plan: bool = False) -> None:
    """Seed a new race from an existing one (copy-forward on branch). Only the
    template-like data is copied; the course is left empty to be set per race."""
    if fuel:
        segments = get_race_fuel(src_race_id)
        if segments:
            set_race_fuel(segments, race_id=dst_race_id)
    if plan:
        csv_text = get_race_config_blob("plan_csv", src_race_id)
        if csv_text:
            set_race_config_blob("plan_csv", csv_text, race_id=dst_race_id)


_GOAL_FIELDS = {
    "race_name", "race_date", "distance_km", "vert_m",
    "aspirational_time_sec", "realistic_min_sec", "realistic_max_sec",
    "status", "notes",
}


def update_goal(goal_id: int, **fields) -> dict | None:
    updates = {k: v for k, v in fields.items() if k in _GOAL_FIELDS and v is not None}
    if not updates:
        return get_goal_by_id(goal_id)
    now = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = {P}" for k in updates)
    params = tuple(updates.values()) + (now, goal_id)
    sql = f"UPDATE goals SET {set_clause}, updated_at = {P} WHERE id = {P}"
    with _conn() as conn:
        _run(conn, sql, params)
    return get_goal_by_id(goal_id)


def create_goal(
    race_name: str, race_date: str, distance_km: float, vert_m: int,
    aspirational_time_sec: int, realistic_min_sec: int, realistic_max_sec: int,
    notes: str = "",
) -> dict | None:
    """Create a new race (goal) and make it the active race. Other races are
    preserved (no longer archived) — switching between them is the job of
    set_active_race_id(). Used by onboarding and the new-race flow."""
    now = datetime.now(timezone.utc).isoformat()
    insert = (
        f"INSERT INTO goals (race_name, race_date, distance_km, vert_m, "
        f"aspirational_time_sec, realistic_min_sec, realistic_max_sec, "
        f"status, notes, created_at, updated_at) "
        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P},{P},{P})"
    )
    params = (
        race_name, race_date, distance_km, vert_m,
        aspirational_time_sec, realistic_min_sec, realistic_max_sec,
        "active", notes, now, now,
    )
    with _conn() as conn:
        if _PG:
            with conn.cursor() as cur:
                cur.execute(insert + " RETURNING id", params)
                new_id = cur.fetchone()[0]
        else:
            new_id = conn.execute(insert, params).lastrowid
    set_active_race_id(new_id)
    return get_goal_by_id(new_id)


def save_prediction(
    goal_id: int, predicted_time_sec: int, confidence_low_sec: int, confidence_high_sec: int,
    verdict: str, reasoning: str, signals_json: str = "{}",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"INSERT INTO predictions (goal_id, predicted_at, predicted_time_sec, "
        f"confidence_low_sec, confidence_high_sec, verdict, reasoning, signals_json, created_at) "
        f"VALUES ({P},{P},{P},{P},{P},{P},{P},{P},{P})"
    )
    with _conn() as conn:
        if _PG:
            with conn.cursor() as cur:
                cur.execute(sql + " RETURNING id", (
                    goal_id, now, predicted_time_sec, confidence_low_sec, confidence_high_sec,
                    verdict, reasoning, signals_json, now,
                ))
                return cur.fetchone()[0]
        cur = conn.execute(sql, (
            goal_id, now, predicted_time_sec, confidence_low_sec, confidence_high_sec,
            verdict, reasoning, signals_json, now,
        ))
        return cur.lastrowid


def get_predictions(goal_id: int, limit: int = 50) -> list[dict]:
    sql = f"SELECT * FROM predictions WHERE goal_id = {P} ORDER BY predicted_at ASC LIMIT {P}"
    with _conn() as conn:
        return _fetchall(conn, sql, (goal_id, limit))
