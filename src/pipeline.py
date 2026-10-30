import os
import sys
import webbrowser
import requests
import json
import math
import time
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from src.metrics import calculate_ctl_atl, calculate_tsb, get_target_category

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

REDIRECT_PORT = 5000
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "activity:read_all,activity:read"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'strava_warehouse.duckdb')

STREAMS_TO_FETCH = ["time", "heartrate", "velocity_smooth", "cadence", "watts", "temp", "altitude", "moving"]

# Physiological fallbacks used when an athlete's own values aren't on record.
# Not yet personalized per-athlete (dim_athlete has no resting_heartrate
# column) — see agent_prompting/improvement_plan.md Section 2.
DEFAULT_MAX_HR = 192
DEFAULT_REST_HR = 60


class StravaAuthError(Exception):
    """Raised when a specific stored Strava refresh_token is invalid/revoked
    and can't be silently recovered from (unlike the CLI's get_access_token,
    which falls back to an interactive browser-based OAuth flow — not
    something a web request can do). Callers should surface this as "please
    reconnect Strava" rather than retrying."""
    pass


def _request_with_retry(method, url, max_retries=3, **kwargs):
    """Wraps a requests.get/post call with Strava 429 rate-limit handling:
    reads the Retry-After header (seconds) and sleeps before retrying, up to
    max_retries times, instead of letting a 429 raise_for_status() abort
    ingestion partway through.
    """
    resp = None
    for attempt in range(max_retries + 1):
        resp = method(url, **kwargs)
        if resp.status_code == 429 and attempt < max_retries:
            retry_after = int(resp.headers.get('Retry-After', 15))
            print(f"  Rate limited (429). Waiting {retry_after}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(retry_after)
            continue
        return resp
    return resp

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        code = query_components.get('code', [None])[0]
        if code:
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authorization Successful!</h1><p>You can close this window now.</p></body></html>")
            self.server.auth_code = code
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed.")
            
    def log_message(self, format, *args):
        pass # minimize output

class StravaManager:
    def __init__(self):
        if not CLIENT_ID or not CLIENT_SECRET:
            print("Error: STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env file.")
            sys.exit(1)
            
    def save_refresh_token(self, refresh_token):
        set_key(env_path, "STRAVA_REFRESH_TOKEN", refresh_token)
        load_dotenv(env_path, override=True)

    def run_oauth_flow(self):
        auth_url = f"https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}"
        print(f"Please open URL to authorize: {auth_url}")
        try: webbrowser.open(auth_url)
        except: pass
        
        server = HTTPServer(('localhost', REDIRECT_PORT), OAuthHandler)
        server.auth_code = None
        server.handle_request()
        if server.auth_code: return self.exchange_code_for_token(server.auth_code)
        return None

    def exchange_code_for_token(self, code):
        data = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code, 'grant_type': 'authorization_code'
        }).json()
        self.save_refresh_token(data['refresh_token'])
        return data['access_token']

    def get_access_token(self):
        refresh_token = os.getenv('STRAVA_REFRESH_TOKEN')
        if not refresh_token:
            print("No refresh token found. Starting OAuth flow...")
            return self.run_oauth_flow()
        try:
            print("Refreshing access token...")
            resp = requests.post("https://www.strava.com/oauth/token", data={
                'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'refresh_token': refresh_token, 'grant_type': 'refresh_token'
            })
            if resp.status_code in [400, 401]:
                print(f"Refresh token invalid (status {resp.status_code}). Starting OAuth flow...")
                return self.run_oauth_flow()
            resp.raise_for_status()
            data = resp.json()
            if data.get('refresh_token') and data['refresh_token'] != refresh_token:
                print("New refresh token received, saving...")
                self.save_refresh_token(data['refresh_token'])
            return data['access_token']
        except Exception as e:
            print(f"Error getting access token: {e}")
            return None

    def refresh_access_token(self, refresh_token):
        """Exchanges a specific refresh_token for a fresh access_token,
        without ever falling back to the interactive OAuth flow.

        Unlike get_access_token (which reads the single-tenant
        STRAVA_REFRESH_TOKEN env var and, on failure, opens a browser and
        blocks on a local HTTP server — fine for the CLI, wrong for a web
        request), this is for a *specific* Strava account's own stored
        refresh_token (e.g. a User row's strava_refresh_token). On failure
        it raises StravaAuthError so the caller can tell its user to
        reconnect Strava, rather than hanging the request.

        Returns (access_token, new_refresh_token_or_None) — Strava
        sometimes rotates the refresh token on use; the caller is
        responsible for persisting new_refresh_token if it's not None.
        """
        resp = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': refresh_token, 'grant_type': 'refresh_token'
        })
        if resp.status_code in (400, 401):
            raise StravaAuthError(
                "Strava refresh token is invalid or has been revoked. Reconnect Strava to sync again."
            )
        resp.raise_for_status()
        data = resp.json()
        new_refresh_token = data.get('refresh_token')
        if new_refresh_token == refresh_token:
            new_refresh_token = None
        return data['access_token'], new_refresh_token

    def fetch_activities(self, access_token, count=5, page=1, after=None):
        params = {'per_page': count, 'page': page}
        if after: params['after'] = after
        resp = _request_with_retry(requests.get, "https://www.strava.com/api/v3/athlete/activities", headers={'Authorization': f'Bearer {access_token}'}, params=params)
        if resp.status_code == 401: raise Exception("Unauthorized")
        resp.raise_for_status()
        return resp.json()

    def fetch_activity_streams(self, access_token, activity_id, keys=STREAMS_TO_FETCH):
        print(f"  Fetching streams for activity {activity_id}...")
        resp = _request_with_retry(requests.get, f"https://www.strava.com/api/v3/activities/{activity_id}/streams", headers={'Authorization': f'Bearer {access_token}'}, params={'keys': ",".join(keys), 'key_by_type': 'true'})
        if resp.status_code == 401: raise Exception("Unauthorized")
        if resp.status_code == 404:
            print(f"  Streams not found for activity {activity_id}")
            return None
        resp.raise_for_status()
        return resp.json()

class StravaDB:
    def __init__(self, db_path=DB_PATH):
        self.con = duckdb.connect(db_path)
        self.init_schema()
        
    def _migration_applied(self, name):
        row = self.con.execute("SELECT 1 FROM schema_migrations WHERE name = ?", [name]).fetchone()
        return row is not None

    def _mark_migration_applied(self, name):
        self.con.execute(
            "INSERT INTO schema_migrations (name) VALUES (?) ON CONFLICT (name) DO NOTHING",
            [name]
        )

    def init_schema(self):
        # schema_migrations gives this file's ad hoc CREATE/ALTER-based
        # schema management a visible, queryable history — there's no
        # dedicated migration framework for DuckDB the way Alembic exists
        # for Postgres (see alembic/ + src/api/database.py), so this is the
        # minimum viable version: a named list of migrations, each recorded
        # once applied. New schema changes should add a new named entry here
        # rather than another bare try/except ALTER TABLE.
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name VARCHAR PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.con.execute("""
            CREATE TABLE IF NOT EXISTS dim_athlete (
                athlete_id UBIGINT PRIMARY KEY, username VARCHAR, firstname VARCHAR, lastname VARCHAR,
                city VARCHAR, state VARCHAR, country VARCHAR, sex VARCHAR, weight DOUBLE, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        if not self._migration_applied("dim_athlete_add_weight_column"):
            try:
                self.con.execute("ALTER TABLE dim_athlete ADD COLUMN weight DOUBLE;")
            except duckdb.CatalogException:
                pass
            except duckdb.BinderException:
                pass
            self._mark_migration_applied("dim_athlete_add_weight_column")
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS dim_activity (
                activity_id UBIGINT PRIMARY KEY, athlete_id UBIGINT, name VARCHAR, type VARCHAR,
                start_date TIMESTAMP, start_date_local TIMESTAMP, distance DOUBLE, moving_time INTEGER,
                elapsed_time INTEGER, total_elevation_gain DOUBLE, average_speed DOUBLE, max_speed DOUBLE,
                average_heartrate DOUBLE, max_heartrate DOUBLE, average_cadence DOUBLE, calories DOUBLE,
                device_name VARCHAR, description VARCHAR, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES dim_athlete(athlete_id)
            );
        """)
        def create_stream_table(name, val_type="DOUBLE"):
            # PRIMARY KEY (activity_id, time_offset) makes a fresh table
            # idempotent under upsert. CREATE TABLE IF NOT EXISTS is a no-op
            # for a table that already exists (from before this constraint
            # was added), which is why _migrate_stream_table_to_pk below
            # exists — it detects and fixes that specific case.
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS stream_{name} (
                    activity_id UBIGINT, time_offset INTEGER, value {val_type},
                    FOREIGN KEY (activity_id) REFERENCES dim_activity(activity_id),
                    PRIMARY KEY (activity_id, time_offset)
                );
            """)
            self._migrate_stream_table_to_pk(name, val_type)

        create_stream_table("heartrate", "INTEGER")
        create_stream_table("velocity", "DOUBLE")
        create_stream_table("cadence", "INTEGER")
        create_stream_table("watts", "INTEGER")
        create_stream_table("temperature", "INTEGER")
        create_stream_table("altitude", "DOUBLE")
        create_stream_table("moving", "BOOLEAN")

    def _table_has_pk(self, table_name):
        try:
            row = self.con.execute(
                "SELECT 1 FROM duckdb_constraints() WHERE table_name = ? AND constraint_type = 'PRIMARY KEY' LIMIT 1",
                [table_name]
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _migrate_stream_table_to_pk(self, name, val_type):
        """One-time migration for a stream_* table created before this table
        had a PRIMARY KEY(activity_id, time_offset). Deduplicates existing
        rows (duplicates were confirmed to have identical values — see
        agent_prompting/improvement_plan.md Section 2) and rebuilds the table
        with the constraint in place. No-ops (cheaply) once migrated, since
        _table_has_pk then returns True.
        """
        table = f"stream_{name}"
        if self._table_has_pk(table):
            return
        before = self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  Migrating {table}: adding PRIMARY KEY(activity_id, time_offset) and deduping {before} rows...")
        self.con.execute(f"""
            CREATE TABLE {table}_new (
                activity_id UBIGINT, time_offset INTEGER, value {val_type},
                FOREIGN KEY (activity_id) REFERENCES dim_activity(activity_id),
                PRIMARY KEY (activity_id, time_offset)
            );
        """)
        self.con.execute(f"""
            INSERT INTO {table}_new (activity_id, time_offset, value)
            SELECT activity_id, time_offset, MAX(value)
            FROM {table}
            GROUP BY activity_id, time_offset;
        """)
        after = self.con.execute(f"SELECT COUNT(*) FROM {table}_new").fetchone()[0]
        self.con.execute(f"DROP TABLE {table};")
        self.con.execute(f"ALTER TABLE {table}_new RENAME TO {table};")
        print(f"    {table}: {before} -> {after} rows ({before - after} duplicate(s) removed).")

    def upsert_athlete(self, a):
        self.con.execute("""
            INSERT INTO dim_athlete (athlete_id, username, firstname, lastname, city, state, country, sex, weight) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id) DO UPDATE SET username=EXCLUDED.username, firstname=EXCLUDED.firstname, lastname=EXCLUDED.lastname, city=EXCLUDED.city, state=EXCLUDED.state, country=EXCLUDED.country, sex=EXCLUDED.sex, weight=EXCLUDED.weight, updated_at=now();
        """, [a.get('id'), a.get('username'), a.get('firstname'), a.get('lastname'), a.get('city'), a.get('state'), a.get('country'), a.get('sex'), a.get('weight')])

    def upsert_activity(self, a):
        self.con.execute("""
            INSERT INTO dim_activity (activity_id, athlete_id, name, type, start_date, start_date_local, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, average_cadence, calories, device_name, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (activity_id) DO UPDATE SET name=EXCLUDED.name, type=EXCLUDED.type, distance=EXCLUDED.distance, moving_time=EXCLUDED.moving_time, elapsed_time=EXCLUDED.elapsed_time, total_elevation_gain=EXCLUDED.total_elevation_gain, average_speed=EXCLUDED.average_speed, max_speed=EXCLUDED.max_speed, average_heartrate=EXCLUDED.average_heartrate, updated_at=now();
        """, [a.get('id'), a.get('athlete', {}).get('id'), a.get('name'), a.get('type'), a.get('start_date'), a.get('start_date_local'), a.get('distance'), a.get('moving_time'), a.get('elapsed_time'), a.get('total_elevation_gain'), a.get('average_speed'), a.get('max_speed'), a.get('average_heartrate'), a.get('max_heartrate'), a.get('average_cadence'), a.get('calories'), a.get('device_name'), a.get('description')])

    def insert_streams(self, activity_id, streams):
        if not streams or 'time' not in streams: return
        time_data = streams['time']['data']

        def proc(key, tbl, val_type):
            if key not in streams: return
            values = streams[key]['data']
            if len(values) != len(time_data):
                # Strava can return a shorter stream than 'time' (e.g. a
                # sensor connected partway through). zip() would silently
                # truncate to the shortest and misalign every offset after
                # that point, so skip this stream for this activity entirely
                # instead of inserting misaligned data.
                print(f"    WARNING: stream '{key}' for activity {activity_id} has {len(values)} samples but 'time' has {len(time_data)}; skipping this stream for this activity.")
                return
            # One bulk upsert per stream instead of one statement per sample:
            # a long run is thousands of samples across seven tables. The
            # PRIMARY KEY(activity_id, time_offset) keeps a re-run idempotent.
            rows = pd.DataFrame({'activity_id': activity_id, 'time_offset': time_data, 'value': values})
            self.con.register('stream_rows', rows)
            self.con.execute(
                f"INSERT INTO stream_{tbl} (activity_id, time_offset, value) "
                f"SELECT activity_id, time_offset, CAST(value AS {val_type}) FROM stream_rows "
                f"ON CONFLICT (activity_id, time_offset) DO UPDATE SET value = EXCLUDED.value"
            )
            self.con.unregister('stream_rows')

        proc('heartrate', 'heartrate', 'INTEGER')
        proc('velocity_smooth', 'velocity', 'DOUBLE')
        proc('cadence', 'cadence', 'INTEGER')
        proc('watts', 'watts', 'INTEGER')
        proc('temp', 'temperature', 'INTEGER')
        proc('altitude', 'altitude', 'DOUBLE')
        proc('moving', 'moving', 'BOOLEAN')

    def activity_exists(self, act_id): return self.con.execute("SELECT 1 FROM dim_activity WHERE activity_id = ?", [act_id]).fetchone() is not None

    def activity_has_streams(self, act_id):
        # Checks all seven stream tables, not just stream_velocity — an
        # activity genuinely lacking GPS/velocity data (treadmill run,
        # manual entry) used to fail this check forever even after its other
        # streams were successfully fetched, triggering a full re-fetch (and
        # re-upsert) of all its streams on every single future ingestion run.
        for tbl in ("stream_heartrate", "stream_velocity", "stream_cadence",
                    "stream_watts", "stream_temperature", "stream_altitude", "stream_moving"):
            if self.con.execute(f"SELECT 1 FROM {tbl} WHERE activity_id = ? LIMIT 1", [act_id]).fetchone() is not None:
                return True
        return False

def _no_progress(phase, **info):
    pass


def ingest_data(manager, db, access_token, lookback_days=42, reauth_fn=None, progress=None):
    """reauth_fn is called (with no arguments) if a request comes back 401
    mid-ingestion, and must return a fresh access_token (or None/raise to
    give up). Defaults to manager.run_oauth_flow — the CLI's interactive
    "open a browser and run a local server" flow. A caller running this on
    behalf of a specific web user (see sync_athlete below) must pass a
    non-interactive reauth_fn instead: run_oauth_flow would otherwise try to
    open a browser and block on a local HTTP server on whatever machine is
    running the API process, which can never receive a real callback there.

    progress(phase, **info) is called as the ingest moves along:
    ('listing', page=, found=) once per page of the activity list, then
    ('streams', done=, total=, name=) once per activity whose streams are
    fetched. The web app's sync progress bar is built on these calls.
    """
    if reauth_fn is None:
        reauth_fn = manager.run_oauth_flow
    if progress is None:
        progress = _no_progress

    after_ts = int((datetime.now() - timedelta(days=lookback_days)).timestamp())
    print(f"Limiting ingestion to activities after {datetime.fromtimestamp(after_ts)} ({lookback_days} days lookback)")

    try:
        print("Fetching athlete profile...")
        athlete = requests.get("https://www.strava.com/api/v3/athlete", headers={'Authorization': f'Bearer {access_token}'}).json()
        db.upsert_athlete(athlete)
        print(f"Athlete: {athlete.get('firstname')} {athlete.get('lastname')}")
    except Exception as e:
        print(f"Warning: Could not fetch athlete profile: {e}")

    # Pass 1 lists every activity in the window, so pass 2 knows its total
    # before it starts and the progress bar can show "n of N".
    activities = []
    page = 1
    while True:
        print(f"Fetching activities page {page}...")
        try:
            batch = manager.fetch_activities(access_token, count=50, page=page, after=after_ts)
        except Exception as e:
            print(f"Error fetching activities on page {page}: {e}")
            if "Unauthorized" in str(e):
                print("Token expired during ingest, re-authenticating...")
                access_token = reauth_fn()
                if not access_token: break
                continue
            break

        if not batch:
            print("No more activities found.")
            break

        activities.extend(batch)
        progress('listing', page=page, found=len(activities))
        if len(batch) < 50:
            print("Reached last page of activities.")
            break
        page += 1

    pending = []
    for act in activities:
        act_id = act.get('id')
        if act_id is None:
            print(f"  Skipping malformed activity entry with no id: {act}")
            continue
        if not db.activity_exists(act_id) or not db.activity_has_streams(act_id):
            pending.append(act)

    # Pass 2 makes one streams request per new (or stream-less) activity.
    for position, act in enumerate(pending, start=1):
        act_id = act['id']
        act_name = act.get('name', '(unnamed)')
        print(f"  Syncing {act_name} ({act_id})")
        db.upsert_activity(act)
        try:
            streams = manager.fetch_activity_streams(access_token, act_id)
            if streams:
                db.insert_streams(act_id, streams)
                print("    Streams saved.")
        except Exception as str_e:
            print(f"    Error fetching streams for {act_id}: {str_e}")
            if "Unauthorized" in str(str_e):
                print("    Token expired during stream fetch, re-authenticating...")
                access_token = reauth_fn()
                if access_token:
                    streams = manager.fetch_activity_streams(access_token, act_id)
                    if streams: db.insert_streams(act_id, streams)
        progress('streams', done=position, total=len(pending), name=act_name)
    return len(pending)

def calculate_trimp_banister(duration_sec, avg_hr, max_hr, rest_hr, is_male):
    if max_hr <= rest_hr: return 0
    hr_ratio = max(0, (avg_hr - rest_hr) / (max_hr - rest_hr))
    return (duration_sec / 60.0) * hr_ratio * (0.64 * math.exp((1.92 if is_male else 1.67) * hr_ratio))

def calculate_trimp_edwards(hr_series, max_hr):
    if not hr_series: return 0
    hr_array = np.array(hr_series)
    return ((np.sum((hr_array >= max_hr * 0.5) & (hr_array < max_hr * 0.6)) * 1) + (np.sum((hr_array >= max_hr * 0.6) & (hr_array < max_hr * 0.7)) * 2) + (np.sum((hr_array >= max_hr * 0.7) & (hr_array < max_hr * 0.8)) * 3) + (np.sum((hr_array >= max_hr * 0.8) & (hr_array < max_hr * 0.9)) * 4) + (np.sum(hr_array >= max_hr * 0.9) * 5)) / 60.0

def calculate_time_in_zones(hr_series, max_hr, rest_hr=DEFAULT_REST_HR):
    """Samples in five zones of heart rate reserve (Karvonen): below 60%,
    60-70%, 70-80%, 80-90%, and 90% and up. Same reserve the TRIMP uses."""
    if not hr_series: return [0, 0, 0, 0, 0]
    reserve = (np.array(hr_series, dtype=float) - rest_hr) / max(1, max_hr - rest_hr)
    counts = np.histogram(reserve, bins=[-np.inf, 0.6, 0.7, 0.8, 0.9, np.inf])[0]
    return [int(count) for count in counts]

def calculate_aerobic_decoupling(hr_times, hr_values, vel_times, vel_values):
    if not hr_values or not vel_values: return None
    df = pd.merge(pd.DataFrame({'time': hr_times, 'hr': hr_values}), pd.DataFrame({'time': vel_times, 'vel': vel_values}), on='time', how='inner')
    df = df[df['vel'] > 0.1]
    if len(df) < 60: return None
    duration = df['time'].max()
    midpoint = duration / 2
    fh, sh = df[df['time'] <= midpoint], df[df['time'] > midpoint]
    if len(fh) == 0 or len(sh) == 0: return None
    ef1, ef2 = (fh['vel'].mean() * 60) / fh['hr'].mean() if fh['hr'].mean() > 0 else 0, (sh['vel'].mean() * 60) / sh['hr'].mean() if sh['hr'].mean() > 0 else 0
    if ef1 == 0: return 0
    return ((ef1 - ef2) / ef1) * 100

def run_analyze_effectiveness(db, progress=None, resting_hr=None, max_hr=None):
    """Computes per-activity TRIMP/EF/zones/decoupling into
    activity_effectiveness. This is the sole implementation of that math in
    the codebase (see calculate_training_status_logic in src/api/main.py,
    which now just aggregates this table's output) — unify any future change
    here rather than adding a second copy elsewhere.

    Only activities that are new, re-synced since their last computation, or
    computed against a different heart rate profile are processed. Each row
    records the max and resting heart rate it used, so changing either
    recomputes every activity on the next run.

    resting_hr and max_hr override the defaults (DEFAULT_REST_HR and the
    athlete's highest recorded heart rate). progress('metrics', done=, total=)
    is called once per processed activity.
    """
    if progress is None:
        progress = _no_progress
    con = db.con
    # Upsert (activity_id is the PRIMARY KEY) instead of DROP+recreate: a
    # crash partway through used to lose every activity's previously-computed
    # row, not just the one that failed.
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_effectiveness (
            activity_id UBIGINT PRIMARY KEY, trimp_banister DOUBLE, trimp_edwards DOUBLE,
            efficiency_factor DOUBLE, intensity_factor DOUBLE, aerobic_decoupling DOUBLE,
            zone_1_sec INTEGER, zone_2_sec INTEGER, zone_3_sec INTEGER, zone_4_sec INTEGER, zone_5_sec INTEGER,
            effectiveness_score DOUBLE, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hr_max_used DOUBLE, hr_rest_used DOUBLE,
            FOREIGN KEY (activity_id) REFERENCES dim_activity(activity_id)
        );
    """)
    if not db._migration_applied("activity_effectiveness_add_hr_profile_columns"):
        for column in ("hr_max_used", "hr_rest_used"):
            try:
                con.execute(f"ALTER TABLE activity_effectiveness ADD COLUMN {column} DOUBLE")
            except (duckdb.CatalogException, duckdb.BinderException):
                pass
        db._mark_migration_applied("activity_effectiveness_add_hr_profile_columns")
    rest_hr = resting_hr or DEFAULT_REST_HR

    # Activities with a heartrate stream get a real per-sample TRIMP.
    # Activities with only a Strava-reported average_heartrate (no stream —
    # e.g. an activity type Strava didn't record HR samples for) still get a
    # single-point TRIMP from that average, instead of being silently
    # excluded from training load entirely (previously indistinguishable
    # from a rest day).
    # Zones and intensity are relative to the athlete's max heart rate (set
    # on the profile, or the highest on record), the same figure the API uses.
    athlete_max_hr = dict(con.execute(
        "SELECT athlete_id, MAX(max_heartrate) FROM dim_activity WHERE max_heartrate > 100 GROUP BY athlete_id"
    ).fetchall())

    candidates = con.execute("""
        SELECT da.activity_id, da.name, da.start_date_local, da.athlete_id, da.average_speed, ath.sex,
               da.average_heartrate, da.moving_time,
               ae.activity_id IS NOT NULL AS computed, ae.hr_max_used, ae.hr_rest_used,
               ae.updated_at < da.updated_at AS resynced
        FROM dim_activity da
        JOIN dim_athlete ath ON da.athlete_id = ath.athlete_id
        LEFT JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
        WHERE EXISTS (SELECT 1 FROM stream_heartrate sh WHERE sh.activity_id = da.activity_id)
           OR (da.average_heartrate IS NOT NULL AND da.average_heartrate > 0)
    """).fetchall()
    acts = []
    for row in candidates:
        athlete_id, computed, used_max, used_rest, resynced = row[3], row[8], row[9], row[10], row[11]
        user_max_hr = max_hr or athlete_max_hr.get(athlete_id) or DEFAULT_MAX_HR
        if computed and not resynced and used_max == user_max_hr and used_rest == rest_hr:
            continue
        acts.append(row[:8] + (user_max_hr,))

    processed = 0
    total = len(acts)
    for i, (act_id, name, date, athlete_id, avg_speed, sex, avg_hr_summary, moving_time, user_max_hr) in enumerate(acts):
        progress('metrics', done=i, total=total)
        if i % 10 == 0:
            print(f"  Processing {i}/{total}: {name}...")

        try:
            hr_data = con.execute(
                "SELECT value, time_offset FROM stream_heartrate WHERE activity_id = ? ORDER BY time_offset",
                [act_id]
            ).fetchall()
            # Filter out NULL samples (a sensor dropout mid-activity) before
            # aggregating — an unfiltered sum() over a NULL raises TypeError
            # and used to abort this entire function partway through.
            hr_data = [(v, t) for (v, t) in hr_data if v is not None]

            # Sex-coefficient default: unknown/missing sex assumes male
            # (1.92). This used to default to female (1.67) here but male
            # elsewhere in the codebase for the same missing-data case —
            # picking one convention and applying it everywhere it's needed
            # now that this is the only place TRIMP is computed.
            is_male = (sex != 'F')

            if hr_data:
                hr_vals, times = [h[0] for h in hr_data], [h[1] for h in hr_data]
                avg_hr = sum(hr_vals) / len(hr_vals)
                trimp_b = 0
                prev_t = times[0]
                for idx in range(1, len(hr_vals)):
                    dt = (times[idx] - prev_t) / 60.0
                    if dt > 5: dt = 0
                    trimp_b += calculate_trimp_banister(dt * 60, hr_vals[idx], user_max_hr, rest_hr, is_male)
                    prev_t = times[idx]

                trimp_e = calculate_trimp_edwards(hr_vals, user_max_hr)
                zones = calculate_time_in_zones(hr_vals, user_max_hr, rest_hr)
                ef = ((avg_speed or 0) * 60) / avg_hr if avg_hr > 0 else 0
                intensity = avg_hr / user_max_hr if user_max_hr else 0

                vel_data = con.execute(
                    "SELECT value, time_offset FROM stream_velocity WHERE activity_id = ? ORDER BY time_offset",
                    [act_id]
                ).fetchall()
                decoupling = calculate_aerobic_decoupling(times, hr_vals, [v[1] for v in vel_data], [v[0] for v in vel_data]) if vel_data else None
            elif avg_hr_summary and moving_time:
                # No stream — single-point TRIMP from the Strava summary.
                avg_hr = avg_hr_summary
                trimp_b = calculate_trimp_banister(moving_time, avg_hr, user_max_hr, rest_hr, is_male)
                trimp_e = 0
                zones = [0, 0, 0, 0, 0]
                ef = ((avg_speed or 0) * 60) / avg_hr if avg_hr > 0 else 0
                intensity = avg_hr / user_max_hr if user_max_hr else 0
                decoupling = None
            else:
                continue

            con.execute("""
                INSERT INTO activity_effectiveness (activity_id, trimp_banister, trimp_edwards, efficiency_factor, intensity_factor, aerobic_decoupling, zone_1_sec, zone_2_sec, zone_3_sec, zone_4_sec, zone_5_sec, effectiveness_score, hr_max_used, hr_rest_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (activity_id) DO UPDATE SET
                    trimp_banister = EXCLUDED.trimp_banister, trimp_edwards = EXCLUDED.trimp_edwards,
                    efficiency_factor = EXCLUDED.efficiency_factor, intensity_factor = EXCLUDED.intensity_factor,
                    aerobic_decoupling = EXCLUDED.aerobic_decoupling, zone_1_sec = EXCLUDED.zone_1_sec,
                    zone_2_sec = EXCLUDED.zone_2_sec, zone_3_sec = EXCLUDED.zone_3_sec, zone_4_sec = EXCLUDED.zone_4_sec,
                    zone_5_sec = EXCLUDED.zone_5_sec, effectiveness_score = EXCLUDED.effectiveness_score,
                    hr_max_used = EXCLUDED.hr_max_used, hr_rest_used = EXCLUDED.hr_rest_used, updated_at = now()
            """, [act_id, trimp_b, trimp_e, ef, intensity, decoupling, zones[0], zones[1], zones[2], zones[3], zones[4], trimp_b, user_max_hr, rest_hr])
            processed += 1
        except Exception as e:
            # One bad activity no longer aborts the whole batch — and
            # because the table is upserted rather than dropped-and-rebuilt,
            # every other activity's previously-computed row survives even
            # if this one keeps failing.
            print(f"  WARNING: failed to compute effectiveness for activity {act_id} ({name}): {e}")
            continue
    return processed

def run_analyze_training_load(db):
    con = db.con
    df = con.execute("SELECT da.start_date_local::DATE as activity_date, SUM(ae.trimp_banister) as daily_load, AVG(ae.efficiency_factor) as daily_ef, AVG(ae.aerobic_decoupling) as daily_decoup FROM activity_effectiveness ae JOIN dim_activity da ON ae.activity_id = da.activity_id GROUP BY 1 ORDER BY 1").fetchdf()
    if df.empty: return None
    
    start_date, end_date = df['activity_date'].min(), datetime.now().date()
    daily_data = pd.DataFrame({'date': pd.date_range(start=start_date, end=end_date, freq='D').date})
    daily_data['activity_date'] = pd.to_datetime(daily_data['date'])
    df['activity_date'] = pd.to_datetime(df['activity_date'])
    
    merged = pd.merge(daily_data, df, on='activity_date', how='left')
    merged['daily_load'] = merged['daily_load'].fillna(0)
    ctl, atl = calculate_ctl_atl(merged['daily_load'].values)
    merged['CTL'], merged['ATL'] = ctl, atl
    merged['TSB'] = calculate_tsb(ctl, atl)
    
    today = merged.iloc[-1]
    return {"CTL": today['CTL'], "ATL": today['ATL'], "TSB": today['TSB']}


def recompute_metrics(db_path=None, progress=None, resting_hr=None, max_hr=None):
    """Re-runs the effectiveness and training-load passes without talking to
    Strava, for when the athlete changes their heart rate profile. Returns
    (None, 0) so it runs under the same job runner as sync_athlete."""
    if progress is None:
        progress = _no_progress
    db = StravaDB(db_path=db_path or DB_PATH)
    try:
        progress('metrics')
        run_analyze_effectiveness(db, progress=progress, resting_hr=resting_hr, max_hr=max_hr)
        progress('load')
        run_analyze_training_load(db)
    finally:
        db.con.close()
    return None, 0


def sync_athlete(refresh_token, lookback_days=180, db_path=None, progress=None, resting_hr=None, max_hr=None):
    """Runs one full ingest + effectiveness + training-load cycle for a
    single athlete, using their own stored refresh_token — this is what
    backs the web app's on-demand "Sync Strava Now" action
    (POST /api/sync-strava/{user_id} in src/api/main.py), as opposed to the
    CLI's main() below, which is single-tenant (STRAVA_REFRESH_TOKEN in
    .env) and interactive on auth failure.

    Returns (new_refresh_token_or_None, new_activity_count). Raises
    StravaAuthError if the refresh_token is invalid/revoked — the caller
    (the API route) is expected to turn that into a "reconnect Strava" error
    response rather than opening a browser.

    progress(phase, **info) receives the phases 'auth', 'listing', 'streams',
    'metrics' and 'load' in that order; see src.api.services.sync for the
    consumer.

    NOTE: DuckDB allows only one read-write connection to a file at a time.
    A sync can take anywhere from a few seconds to a couple of minutes
    depending on how many new activities there are (one streams request
    per new activity), during which other requests that need to write to
    the same warehouse file may block or fail. This is an inherent
    DuckDB constraint, not something this function works around — see
    agent_prompting/improvement_plan.md.
    """
    if progress is None:
        progress = _no_progress
    progress('auth')
    manager = StravaManager()
    access_token, new_refresh_token = manager.refresh_access_token(refresh_token)

    def _reauth_unavailable():
        # ingest_data calls this if a request 401s mid-sync (the access
        # token we just fetched is normally valid for 6 hours, so this is
        # very unlikely to fire within a single sync run). There's no
        # browser to open here — surface a clear error instead of hanging.
        raise StravaAuthError("Strava access expired mid-sync. Reconnect Strava and try again.")

    db = StravaDB(db_path=db_path or DB_PATH)
    try:
        count = ingest_data(manager, db, access_token, lookback_days=lookback_days, reauth_fn=_reauth_unavailable, progress=progress)
        progress('metrics')
        run_analyze_effectiveness(db, progress=progress, resting_hr=resting_hr, max_hr=max_hr)
        progress('load')
        run_analyze_training_load(db)
    finally:
        db.con.close()

    return new_refresh_token, count


def main():
    manager = StravaManager()
    access_token = manager.get_access_token()
    if not access_token:
        print("Failed to authenticate with Strava.")
        return
        
    db = StravaDB()
    # 180 days, not 42: CTL uses a 42-day EWMA time constant, and a
    # zero-seeded EWMA only reaches ~63% of steady state after exactly one
    # time constant's worth of days — a 42-day ingestion window meant CTL
    # was systematically understated for every user, worst right after a
    # fresh backfill. 180 days (~4x the constant) gives it room to converge
    # before "today" (see agent_prompting/improvement_plan.md Section 2).
    lookback_days = 180
    print(f"Ingesting Strava data (past {lookback_days} days)...")
    count = ingest_data(manager, db, access_token, lookback_days=lookback_days)
    print(f"Ingestion complete ({count} new activities).")
    
    print("Analyzing effectiveness...", end='', flush=True)
    eff_count = run_analyze_effectiveness(db)
    print(f" Done ({eff_count} activities processed).")
    
    print("Calculating training load...", end='', flush=True)
    stats = run_analyze_training_load(db)
    if stats:
        print(f" Done.\n-> Status: CTL: {stats['CTL']:.1f}, ATL: {stats['ATL']:.1f}, TSB: {stats['TSB']:.1f} ({get_target_category(stats['TSB'])})")
    else:
        print(" Failed (no data).")

    db.con.close()

if __name__ == "__main__":
    main()
