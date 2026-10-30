# Race Planner

A self-hosted training log and planner for runners, built on your Strava data.
It pulls every activity and its streams into a local DuckDB warehouse, works out training load, heart-rate zones, VO2 max, race predictions and aerobic efficiency from them, and plans the weeks to your next race.
Every estimator is a short, readable function (mostly in `src/metrics.py`), so you can see how each number is made, and argue with it.

## Features

*   **Fitness, fatigue and form**: Banister TRIMP on heart-rate reserve, rolled up into CTL, ATL and TSB, and projected forward along your planned sessions.
*   **Training plan calendar**: plan sessions by hand or let **Build plan** lay out the weeks to a race (ramp, cutback weeks, taper), with what you actually ran next to each day.
*   **Activity analysis**: streams with laps and splits, detected interval and tempo sessions, time in heart-rate zones, aerobic decoupling, and an efficiency trend across your easy runs; planned sessions are marked done when a run matches them.
*   **Race predictions and records**: predictions from your best efforts (Riegel), with the VDOT equivalent of your VO2 max as a ceiling, personal records, and a race-day pacing plan that follows the course's hills.
*   **Strava sync** with a live progress bar, or automatically through Strava webhooks.
*   **Weekly digest email** on Monday mornings.
*   **COROS (optional, unofficial)**: overnight resting heart rate and HRV, a daily readiness call, and the resting heart rate the zones use.
*   **Web UI**: server-rendered pages with a small dependency-free SVG chart module (see `src/api/static/js/charts/AGENTS.md`), dark and light themes, installable on a phone.

## Not medical or coaching advice

The numbers here are estimates from heuristics (heart-rate-reserve zones, ACSM running cost, Riegel's formula, Daniels' VDOT tables, a rule-based plan builder) applied to whatever your watch recorded, and they can be wrong by a lot.
Treat them as a training log's opinion, not as medical or coaching advice; if something hurts or feels off, ask a person who knows.

## Run it yourself

### Try the demo first

The demo needs no Strava account, API keys or database of your own, only Docker:

```bash
docker compose -f docker-compose.demo.yml up --build
```

Open http://localhost:8000 and log in as `demo@example.com` / `demo1234`.
Inside the container, `scripts/demo.py` serves a fake Strava and a fake COROS (the same stand-ins the browser tests use), with about six months of made-up runs and two months of nights, and a half marathon ten weeks out with a plan built for it.
Nothing leaves your machine.
`DEMO_PORT=8080` picks another port, and `docker compose -f docker-compose.demo.yml down` throws it all away.

Without Docker, point the demo at a Postgres database it may migrate; it writes only the demo user's rows and keeps its own warehouse in `data/demo/`:

```bash
DATABASE_URL=postgresql://user:pass@localhost/raceplanner python scripts/demo.py
```

### What you need

*   Python 3.12 and Postgres 15 or later.
*   A Strava account, to register an API application.
*   Optional: an SMTP account for password-reset and digest emails, a COROS account, an API key for one of the LLM providers (for the coach's note on the dashboard).

### 1. Register a Strava API application

At https://www.strava.com/settings/api, create an application.
Set **Authorization Callback Domain** to the host the app runs on: `localhost` on your own machine, your domain on a server.
The app sends Strava to `HOST_URL/strava-callback`, and Strava only checks the domain.
Copy the **Client ID** and **Client Secret** into `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET`.

Strava limits how many requests an application makes per 15 minutes and per day.
A first sync reads the last 180 days and makes about two requests per activity (streams and laps); if a long history runs into the limit, the next sync carries on where it stopped.

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill it in:

| Variable | What it is |
|---|---|
| `DATABASE_URL` | Postgres for users, races and plans. |
| `DB_PATH` | The DuckDB warehouse file (default `data/strava_warehouse.duckdb`). |
| `SECRET_KEY` | Signs session cookies and reset links; `python -c "import secrets; print(secrets.token_hex(32))"`. Without it, everyone is logged out on every restart. |
| `SECURE_COOKIES` | `true` when the site is served over HTTPS. |
| `HOST_URL` | The public address of the app, for the Strava redirect and reset links. |
| `ALLOWED_ORIGINS` | Origins allowed to make credentialed requests (default `http://localhost:8000`). |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | From your Strava application. |
| `STRAVA_WEBHOOK_VERIFY_TOKEN` | Optional, see [Strava webhooks](#strava-webhooks-automatic-sync). |
| `STRAVA_REFRESH_TOKEN` | Only for the single-athlete command-line pipeline. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional; without `SMTP_HOST`, emails are written to the server log. |
| `APP_URL` | The link at the foot of the weekly digest. |
| `LLM_PROVIDER`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` | Optional coach's note; `local` means LM Studio on `localhost:1234`, and with no provider reachable the note is simply left out. |
| `COROS_ENABLED` | `false` switches COROS off entirely, see [COROS](#coros-unofficial). |

`STRAVA_API_BASE` and `COROS_API_BASE` point the app at stand-ins for the tests and the demo; leave them unset.

### 3. Create the database

```bash
pip install -r requirements.txt
createdb raceplanner
alembic upgrade head
```

Alembic manages the Postgres schema; the DuckDB warehouse creates its own tables on first start.

### 4. Run it

```bash
python -m uvicorn src.api.main:app --reload
```

Open http://localhost:8000, register, connect Strava, and press **Sync Strava**.

`docker compose up --build` is the same with a Postgres next to the app.
It builds `src/api/Dockerfile` (with `--reload` and the source mounted); the root `Dockerfile` is a production-style image (migrations, then uvicorn on `$PORT`), which the demo also uses.

### Running it on a server

`deploy/` is how the author's install runs: Ubuntu 22.04, the app as a systemd service behind nginx, and deploys from CI.
The scripts were written for that one box, so read them before running them on yours.

1.  `deploy/bootstrap.sh ENV_FILE [WAREHOUSE]` (as root, once) installs Postgres listening only on its Unix socket, creates the `raceplanner` user and database and a Python 3.12 venv in `/opt/race-planner`, and installs the env file.
2.  Put the code in `/opt/race-planner/app` (or let CI deploy it, below).
3.  `deploy/install.sh` (as root) installs `race-planner.service` (Alembic, then uvicorn on `127.0.0.1:8000`), a sudoers rule that lets the service user restart only its own service, and the deploy hook.
4.  Point nginx at the app and get a certificate with `certbot --nginx -d your.domain`, then set `HOST_URL=https://your.domain` and `SECURE_COOKIES=true`:

    ```nginx
    server {
        server_name your.domain;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
    ```

`.github/workflows/ci.yml` runs the unit and browser tests on every push and, on `main`, pipes the repository over SSH to a key whose only allowed command is `deploy/receive.sh`, which installs the code and restarts the service.
To deploy your own fork that way, put your public key in `deploy/ci_deploy_key.pub`, the private key in the `DEPLOY_SSH_KEY` repository secret, your server's host key in the `DEPLOY_KNOWN_HOSTS` variable, and your host name in the workflow's deploy step.

## Using it

### Syncing from Strava
The **Sync Strava** button pulls new activities, their streams and laps, then recomputes the metrics, with a progress bar while it runs.
With a [webhook subscription](#strava-webhooks-automatic-sync), Strava starts the sync itself whenever you upload.

### Building a training plan
On the training plan page, **Build plan** fills the calendar from tomorrow to one of your upcoming races.
The generator (`src/plan_builder.py`) is deterministic: weekly distance starts from your last four weeks of running,
climbs no more than 10% a week toward a peak that suits the race distance, drops back every fourth week and tapers before the race.
Each week has a long run on the day you choose, up to two quality sessions (tempo and intervals), easy runs on the other training days and rest otherwise.
Every entry is an ordinary calendar entry afterwards, so edit or delete whatever does not fit.
Building again replaces planned days after today unless you untick that option, in which case only empty days are filled.

### Race-day pacing
Each race on the profile page has a **Pacing** page: split targets for your goal time (the race's goal, or your predicted time from recent best efforts when there is none), as even effort or a negative split (second half about 2% faster), in miles or kilometres.
Upload the course GPX and the splits follow the hills at one effort: each split's pace carries the grade cost of its ground (Minetti et al. 2002, the relation grade-adjusted pace is built on), with downhills giving back half the saving, and the whole plan still adds up to the goal to the second.
**Print pace band** prints only a narrow strip of split clock times to wear on race day.

### Detected intervals and automatic plan completion
Every sync looks at the pace stream of each new run and calls it **intervals**, **tempo** or nothing (`src/intervals.py`, which documents every threshold).
In short: pace is resampled to one reading a second, adjusted for grade with the same ACSM cost the VO2 max estimator uses, smoothed, and split into a fast and a slow group; the fast group has to be at least 12% faster than the slow one.
Three or more reps with a median under 8 minutes are intervals; one or two fast blocks of 12 minutes or more are a tempo.
The activity page then shows a **Detected structure** card (for example "6 × 800 m at 3:42/km, 2:38 recoveries") with a table of the reps, and shades the reps on the stream chart.
Runs synced before this feature are analysed on the next sync.

After each sync, planned sessions from the last seven days are marked done when a run matches them (`src/api/services/plan_match.py`): an Intervals or Tempo session needs a run with that structure, a Long Run needs 80% of the planned distance or the longest run of that week, an Easy Run or Race needs any run that day, and Cross Training or Strength needs any activity that day.
On the dashboard, a planned Intervals or Tempo day whose runs were analysed and do not match shows **Ran, not the session** instead of Done.

Known blind spots: a run-walk easy run reads as intervals (the walks look like recoveries), and three or more long reps (3 × 10 min) are neither kind.

### Strava webhooks (automatic sync)

With a push subscription, Strava calls the app whenever an activity is created, updated or deleted and the warehouse syncs itself; the Sync button becomes a fallback.

1. Set `STRAVA_WEBHOOK_VERIFY_TOKEN` to a long random string in the app's `.env` (for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`) and restart the app.
2. With the same variables in your shell or `.env`, register the callback once per Strava application, giving the site's base URL:

    ```bash
    python scripts/strava_webhook.py create https://your-host
    python scripts/strava_webhook.py view
    ```

The script registers `https://your-host/api/strava/webhook/<token>`.
Strava does not sign its events, so that secret path is what keeps anyone else from posting fake ones; every other path gets a 404.
Strava validates the URL during `create`, so the app must already be reachable there.

### Weekly digest email
Tick **Weekly digest** on the profile page to get a Monday morning email: last week's runs against the week before, how the plan went, fitness and form, new best efforts, this week's sessions and upcoming races.
Sending needs SMTP in `.env`, the same variables the password reset uses: `SMTP_HOST`, `SMTP_PORT` (587 for STARTTLS, 465 for implicit TLS), `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM`.
Set `APP_URL` for the link in the footer.
Without `SMTP_HOST` the digest is written to the server log, and **Preview** on the profile page shows it in the browser.
A thread in the app checks every half hour and sends after 06:00 server time on Mondays; **Send now** sends it straight away.

### COROS (unofficial)
The COROS card on the profile page links a COROS Training Hub account and pulls the nightly readings the watch uploads: resting heart rate, overnight HRV with its baseline and training load. (Sleep is only in the phone app's API, which the app does not use because logging in there signs the phone app out; `scripts/coros_probe.py` prints the raw fields if you want to look for more.)
They show up in the Recovery card on the dashboard together with a readiness call for today (Go, Normal, Easy or Rest, from last night's HRV against its baseline, resting heart rate against your usual and your form; a hard session planned on an Easy or Rest day gets a hint), and the 14-night median resting heart rate becomes the default for the heart-rate-reserve model whenever you have not typed a resting heart rate on your profile (a change of 2 bpm or more recomputes your metrics, as a profile change does).
Linking sends your email and password to COROS once, the way their web app does; only the session token is stored.
The app syncs once a day after 05:00 server time and the card has **Sync now**.
This uses the private API behind the Training Hub web app, not a public one: COROS can change it at any time, it rate-limits, and logging in to the Training Hub elsewhere can invalidate the stored session, in which case the card asks you to reconnect.

Because of that, an install can switch COROS off with `COROS_ENABLED=false`.
The profile card, the dashboard's Recovery card and the COROS routes are then left out, and the nightly pull stops; readings already stored stay in the warehouse.

## Maintenance

### Running the pipeline from the command line
To sync a single athlete from the command line instead of the web app (it uses `STRAVA_REFRESH_TOKEN` and opens a browser to authorize when that is missing):
```bash
python src/pipeline.py
```
The running app holds the warehouse file open, so stop it first (`sudo systemctl stop race-planner` on the server); DuckDB allows one writing process at a time.

### Repairing the warehouse

Stream tables that were migrated with a rename in an earlier version keep a foreign-key reference to their old name inside DuckDB, and every delete on them fails at commit (`Table with name stream_x_new does not exist`). That breaks the activity Delete button and webhook deletes. Rebuild the file once, with the app stopped:

```bash
sudo systemctl stop race-planner
sudo -u raceplanner /opt/race-planner/.venv/bin/python scripts/rebuild_warehouse.py data/strava_warehouse.duckdb
# check the printed row counts, then swap:
sudo -u raceplanner mv data/strava_warehouse.duckdb data/strava_warehouse.duckdb.bak
sudo -u raceplanner mv data/strava_warehouse.rebuilt.duckdb data/strava_warehouse.duckdb
sudo systemctl start race-planner
```

The rebuild copies every table into a fresh schema (about 4 s for 400 activities) and leaves the original untouched until you swap it.

## Project structure

*   **/src/api**: FastAPI backend, Jinja2 templates and the front end (plain ES modules, no build step).
*   **/src/pipeline.py**: The ingestion and analysis engine that calculates TRIMP, EF, and TSB — including `run_analyze_effectiveness()`, which computes per-activity physiological metrics from activity streams.
*   **/src/metrics.py**: Pure math shared by the pipeline and the API: load model, VO2 max estimator, Daniels VDOT, best efforts, planned-load projection.
*   **/src/intervals.py**, **/src/structure_pass.py**: The interval and tempo detector and the sync pass that stores its results.
*   **/src/plan_builder.py**: The deterministic training plan generator behind **Build plan**.
*   **/alembic**: Postgres schema migrations (`alembic upgrade head` to apply). The DuckDB warehouse's own schema is managed separately, inside `src/pipeline.py`'s `StravaDB.init_schema()`.
*   **/tests**, **/e2e**: unit tests (`pytest`), and browser tests against the real app with a fake Strava and a fake COROS (`pytest e2e`, see `e2e/AGENTS.md`).
*   **/scripts**: the demo, the Strava webhook and warehouse repair tools, and a COROS field probe.
*   **/deploy**: the systemd service and the CI deploy hook.
*   **/data**: Contains the `strava_warehouse.duckdb` file.

Most code folders have a short `AGENTS.md` describing what is in them and how the files fit together.

## Methodology
*   **Training Load**: Uses the Banister TRIMP model where `Fitness (CTL)` is a 42-day exponentially weighted moving average (EWMA) and `Fatigue (ATL)` is a 7-day EWMA.
*   **Pace Zones**: Calculated (`calculate_pace_zones()` in `src/api/services/metrics.py`) from a 90-day rolling window: the threshold speed is the 80th percentile of speed samples in the 85–92% max-HR band (or, with fewer than 30 such samples, the 90th percentile of all speed samples as a fallback), then Coros-style zone bands are derived as percentages of that threshold pace.

## License

MIT, see [LICENSE](LICENSE).
