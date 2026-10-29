# Race Planner — Improvement Plan

*Date: 2026-08-06*
*Produced by: automated multi-pass code review (5 parallel focus areas, 2 retried after transport failures), cross-checked against this repo's own `vp_review*.md` / `eng_plan.md` / `qa_review.md` history to verify which past self-reported fixes actually landed in current code.*

## How to use this document

This is a handoff document for whichever engineer or coding agent works on this repo next. Every finding below cites an exact `file:line` and was checked against the actual source (not guessed from the README or from memory) — a few of the data-layer and repo-hygiene findings were additionally verified empirically by querying the live `.duckdb` file or actually running `git`/`pytest` commands, and those are called out explicitly because they're facts, not inferences. Findings are grouped by subsystem, each tagged Critical/High/Medium/Low. Section 7 gives a suggested execution order — **read that before starting**, because a few of these fixes (auth, secrets) need to land before others (refactors, polish) are worth doing, and one item is time-sensitive in a way that has nothing to do with code quality.

---

## ⚠️ Immediate Action Required — Real Personal Data Is Already Live on GitHub

This isn't a "someday" cleanup item. `data/strava_warehouse.duckdb` — a 63–66MB DuckDB file containing Nolan's real Strava activity history, heart-rate streams, and other personal fitness data — is tracked in git despite `.gitignore` supposedly excluding it (the ignore rule was added *after* the file was first committed, so it never took effect). It has been committed 4 times, growing 12MB → 39MB → 58MB → 63MB, and is now 66MB uncommitted in the working tree.

The repo review confirmed this by actually running the commands, not by inference:
- `git remote -v` → `origin  git@github.com:nolangormley/race-planner.git`
- `git ls-remote origin` and `git rev-list --left-right --count HEAD...origin/main` → **0 ahead / 0 behind** — the local `HEAD` commit containing the 63MB personal-data blob is the exact same commit currently on GitHub, right now.

**Before doing anything else in this plan:**
1. Check whether `nolangormley/race-planner` on GitHub is public or private, right now.
2. If it is or ever was public, treat the data as exposed — rotate the Strava OAuth refresh token in `.env` immediately (it's a credential, not just data), since it has been sitting in a file whose *contents* aren't secret but whose presence next to real account data increases blast radius if this repo is ever shared.
3. Decide whether to rewrite git history (`git filter-repo` or BFG Repo-Cleaner) to strip the file from every past commit, then force-push. This is destructive to shared history — coordinate before doing it if anyone else has cloned the repo.
4. At minimum, stop it from getting worse today: `git rm --cached data/strava_warehouse.duckdb` and commit. The `.gitignore` rule is already correct; it just needs the file untracked for it to matter.

The same root cause (a `.gitignore` rule added after a file was already tracked) also applies to `.env.dev` — see Section 1.2. It currently holds only blank placeholder values in every commit, so nothing has leaked through it yet, but it's a live footgun: the moment real secrets are filled in locally and a routine `git commit -a` runs, they go straight to the same GitHub remote.

---

## Executive Summary

The application's actual analytics logic (Banister TRIMP, CTL/ATL/TSB EWMA, VO2 max estimation) is sound and matches the README's stated methodology where it's shared code. Everything *around* that core is where the real risk lives: there is effectively no access control on the API, passwords are stored in plaintext, a real production database already has thousands of duplicate rows from a missing uniqueness constraint, and the same fitness metric is computed two different, disagreeing ways in different parts of the app. The AI integration was patched reactively (three rounds of self-authored "VP review" documents chased specific symptoms) rather than architecturally, so fixes that were verified as real often only cover the one screen that was being looked at when the bug was reported.

The ten items below are the highest-priority items across all areas; each is detailed in its own section further down.

| # | Area | Finding | Severity |
|---|------|---------|----------|
| 1 | Data exposure | Personal Strava health data tracked in git, already synced to a live GitHub remote | **Critical** |
| 2 | Security | ~31 of 37 API routes have zero authentication check — any client can read/edit/delete any user's data | **Critical** |
| 3 | Security | Session "auth" is an unsigned integer cookie; passwords stored and compared in plaintext | **Critical** |
| 4 | Security | CORS config allows credentialed requests from any origin | **Critical** |
| 5 | Security / Frontend | AI- and Strava-sourced text is written into the DOM via unescaped `innerHTML`, with a real traceable prompt-injection path | **Critical** |
| 6 | Data integrity | No uniqueness constraint on stream tables — 46,555 confirmed duplicate rows already in the production `.duckdb` file | **Critical** |
| 7 | Data integrity | Two independent, disagreeing implementations of TRIMP/CTL/ATL/EF (CLI pipeline vs. live API), including a 60x unit bug in one | **Critical** |
| 8 | Data integrity | A single `NULL` heart-rate sample crashes the batch job and can wipe out the entire effectiveness table (drop-and-rebuild with no transaction) | **Critical** |
| 9 | DevOps/Testing | Running `pytest` from repo root makes live, billed calls to Gemini/Groq APIs merely by importing test files — empirically confirmed (34.78s network stall on collection) | **Critical** |
| 10 | Architecture | `src/api/main.py` is a single 1,952-line file mixing routing, raw SQL, metrics math, and three duplicated LLM-provider integrations | High |

---

## 1. Security & Access Control

**Overall state:** Input validation is actually a strength — POST/PUT bodies consistently use Pydantic models, there's no raw unvalidated `request.json()` anywhere. But authentication is close to decorative: only 6 of 37 routes check a session at all, and the "session" they check has no cryptographic integrity. This needs to be fixed before anything else in the app matters, because every other subsystem (AI endpoints, training plan CRUD, profile data) inherits this exposure.

### [Critical] No authentication/authorization on almost all data API routes (IDOR/BOLA)
- **File:line**: `src/api/main.py` — 31 of 37 routes, e.g. `133` (`/api/user-activities/{user_id}`), `168` (`/api/mileage-summary/{user_id}`), `395` (`DELETE /api/activities/{activity_id}`), `880` (`/status/{user_id}`), `1391` (`POST /settings/{user_id}`), `1420`/`1438`/`1460` (races CRUD), `1499` (`POST /profile/{user_id}`), `1608`–`1936` (all 8 `/api/training-plan/...` routes).
- **Problem**: Only 6 routes anywhere in the file check the session cookie at all (the HTML page routes), verified via `grep -n "request.cookies.get" src/api/main.py`. Every JSON API route takes `user_id`/`activity_id`/`race_id` straight from the URL with no session check and no dependency injection (`grep -n "Depends(" src/api/main.py` returns nothing in this file).
- **Why it matters**: Anyone who can reach the API can read or overwrite any other user's profile, race goals, training plan, and chat history, or delete any activity/race by guessing/incrementing small integer IDs — e.g. `POST /settings/17` or `DELETE /api/races/3`, no credentials required. Even the 6 "protected" routes only check "is *someone* logged in," never "does this resource belong to this session."
- **Fix**: Add a shared FastAPI dependency (`get_current_user(request: Request) -> User`, raising 401 on missing/invalid session) and apply via `Depends()` to every user-scoped route. Stop trusting `{user_id}` path params — derive the acting user from the session, and either drop the redundant path param or assert it matches.

### [Critical] Session identity is an unsigned, guessable raw integer cookie
- **File:line**: `src/api/auth_routes.py:48,87` (sets it), `:31-35` (checks it); duplicated inline in `src/api/main.py:70,77,90,102,1480,1539`
- **Problem**: `response.set_cookie(key="user_id", value=str(user.id))` — the literal database primary key is the entire session token. No signature, no server-side session store, no expiry, and `set_cookie` is called with no `httponly`/`secure`/`samesite` args.
- **Why it matters**: Any client can set `Cookie: user_id=1` and be fully authenticated as that user — no password needed. Not `httponly` either, so any XSS on the origin (see finding below) can also read it via `document.cookie`.
- **Fix**: Replace with Starlette's `SessionMiddleware` (signed cookie) or a server-side session store keyed by a random opaque token. Set `httponly=True`, `secure=True` in production, explicit `samesite`.

### [Critical] Passwords stored and compared in plaintext
- **File:line**: `src/api/auth_routes.py:43` (login: `User.password == password`), `:78-81` (registration: raw value stored as-is)
- **Problem**: No hashing library is even a dependency — `grep` for `bcrypt|passlib|argon2|hashlib|werkzeug.security` across the repo returns nothing.
- **Why it matters**: A DB dump or backup reveals every user's password in cleartext, which is also a credential-stuffing risk against other sites given password reuse.
- **Fix**: Add `passlib` or `argon2-cffi`; hash on registration, verify on login; force a password reset for existing plaintext rows since they can't be un-hashed retroactively.

### [Critical] CORS allows credentialed requests from any origin
- **File:line**: `src/api/main.py:37-43`
- **Problem**:
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
  `allow_origins=["*"]` combined with `allow_credentials=True` is invalid per the CORS spec, so Starlette falls back to reflecting whatever `Origin` header the request sent — in effect, any origin is allowed.
- **Why it matters**: Combined with the missing-auth finding above, this lets a malicious webpage's JavaScript running in a victim's browser silently `fetch()` this API cross-origin and read the (already-unauthenticated) responses at scale.
- **Fix**: Set `allow_origins` to an explicit trusted list; drop `allow_credentials=True` unless genuinely needed (this app's frontend is server-rendered by the same origin, so it likely isn't).

### [Critical] AI/Strava-sourced text injected into the DOM via unescaped `innerHTML` — real, traceable XSS/prompt-injection chain
*(Found independently by both the frontend review and the AI-integration review — strong corroboration.)*
- **File:line**: `src/api/templates/dashboard.html:419,438,445` (unsafe sink), `740` (call site)
- **Problem**: The dashboard's "AI Executive Summary" card renders LLM output through a hand-rolled markdown converter that unconditionally skips escaping in one branch and never escapes in the others:
  ```js
  function mdToHtml(text) {
      if (!text) return '';
      if (text.trim().startsWith('<')) return text;   // returns RAW, unescaped, verbatim
      ...
      html += `<li style="...">${bulletMatch[1]}</li>`;   // unescaped
      ...
      html += `<p style="...">${cleanLine}</p>`;          // unescaped
  }
  ...
  document.getElementById('ai-insight').innerHTML = mdToHtml(data.ai_insight);
  ```
  The system prompt that generates this text itself uses `<` in its reference bands (e.g. `"CTL: <40 Developing"`), so a model reply that echoes that style — or is manipulated into it — can trigger the raw passthrough branch. There's a real, traceable data path into this sink: `race_length`/`race_goal_time` profile fields (free-text, saved via `/setup-race` with no validation, `main.py:1386-1389`) and Strava activity `name`/`description` fields (synced verbatim, no cleaning, `pipeline.py:168-173`) both flow into LLM prompts (`main.py:748-765`, `731-732`) whose output is later dropped into this same unescaped sink. The codebase already knows the correct pattern — `training_calendar.html`'s chat renderer uses `textContent` and a proper `escapeHtml()` helper (lines 1660-1664, 1747) — it just wasn't applied here.
- **Why it matters**: A race name or Strava activity description containing `<img src=x onerror=...>` can execute in the victim's own session when the dashboard loads, which can then read the non-`httponly` session cookie (see above) and act as that user.
- **Fix**: Delete the `startsWith('<')` passthrough entirely. Reuse the existing `escapeHtml()` pattern from `training_calendar.html` before building any `<li>`/`<p>` markup. Also worth length-capping and sanitizing free-text profile fields and Strava-sourced text server-side before they ever reach a prompt. Lower-severity instances of the same unescaped-`innerHTML`-of-user-data pattern (self-XSS against one's own stored data, not cross-user) also exist in `profile.html:507-521` (race list) and `index.html:274-291,336-352` (activity/workout tables) and should get the same treatment while this is being fixed.

### [High] Reflected XSS in `/strava-callback` error parameter
- **File:line**: `src/api/auth_routes.py:140,146`
- **Problem**: `error` is an attacker-controllable query parameter, interpolated directly into a raw HTML string with zero escaping: `return HTMLResponse(f"Strava authentication returned an error: {error}. <a href='/strava-auth'>Try again</a>")`.
- **Why it matters**: A crafted `/strava-callback?error=<script>...</script>` link executes attacker JS in the app's own origin for a logged-in victim.
- **Fix**: `html.escape(error)` before interpolating, or render through a Jinja2 template (autoescaping is already correctly on everywhere else in the app — no `|safe` or `autoescape off` exists in any of the 10 templates).

### [High] Blocking synchronous calls inside genuinely `async def` routes
- **File:line**: `src/api/auth_routes.py:161` (`requests.post` for Strava token exchange), and pervasively `43,73-84,92,105-117,123,165-170` (synchronous SQLAlchemy calls)
- **Problem**: Every route in this file is `async def`, yet each does blocking work in the coroutine body with no `run_in_threadpool`/`asyncio.to_thread`. (Note: this is specific to `auth_routes.py` — `main.py` uses plain `def` everywhere, which FastAPI auto-threadpools, so it doesn't have this problem.)
- **Why it matters**: A slow Strava response or DB round-trip during login/OAuth callback freezes the single asyncio event loop for **every concurrent user**, not just the requester.
- **Fix**: Either drop `async def` in favor of plain `def` (matching main.py's pattern) or use `httpx.AsyncClient` + an async SQLAlchemy session throughout this file.

### [Medium] No CSRF protection on state-changing routes
- **File:line**: repo-wide (`grep -rni "csrf" src/` → no matches)
- **Problem**: No CSRF tokens, no double-submit cookie, no `Origin`/`Referer` check. Cookies rely entirely on Starlette's unstated default `SameSite=Lax`.
- **Fix**: Set `samesite`/`httponly`/`secure` explicitly on every `set_cookie` call rather than relying on framework defaults; add anti-CSRF tokens to forms once real auth exists (see Critical finding above).

### [Medium] `SKIP_LOGIN` is primed `true` in the deploy-bound `.env`, but wired to nothing — a landmine for whoever "finishes" it
- **File:line**: `.env:7` (`SKIP_LOGIN=true`), `docker-compose.yml:12` (mounts this exact file into the container)
- **Problem**: An exhaustive repo-wide grep for `SKIP_LOGIN` (case-insensitive) finds **zero** references in any `.py`/template/config — the flag is read nowhere.
- **Why it matters**: The name unambiguously signals an intended login-bypass, and it's already `true` in the file that ships into the container. If a future dev/agent wires up `if os.getenv("SKIP_LOGIN")` for "local dev convenience" without an explicit non-production guard, auth is silently disabled the moment that code merges, because the flag was already primed.
- **Fix**: Delete the unused variable, or implement it with a hard-coded environment guard (e.g. requires `ENV=development` too) and make sure `SKIP_LOGIN=true` never ships in the container's `.env`.

### [Low] Unvalidated cookie value crashes with an unhandled `ValueError`
- **File:line**: `src/api/main.py:81,94,1484,1543`; `src/api/auth_routes.py:35`
- **Problem**: `int(user_id)` is called directly on the raw cookie with no try/except in 5 places.
- **Fix**: Wrap in `try/except ValueError` → redirect to login instead of a generic 500.

### [Medium] `docker-compose.yml` exposes Postgres on the host with hardcoded default credentials
- **File:line**: `docker-compose.yml:20,26-30`; matching hardcoded fallback in `src/api/database.py:6`
- **Problem**: `POSTGRES_USER: user` / `POSTGRES_PASSWORD: password`, with the port published to the host (`"5432:5432"`), and the exact same weak credential baked into `database.py` as the default if `DATABASE_URL` is ever unset.
- **Why it matters**: On any host that isn't strictly local-only, the full app database is reachable directly with a trivially guessable credential, bypassing the API entirely.
- **Fix**: Remove the host `ports:` mapping for `db` (the `api` service reaches it over the internal compose network without one); require `DATABASE_URL` explicitly via `os.environ[...]` (raise if unset) instead of a working default.

---

## 2. Data Pipeline & Metrics Integrity

**Overall state:** The core statistics are genuinely correct — the Banister TRIMP formula and the CTL(42-day)/ATL(7-day) EWMA recurrence in `src/metrics.py:4-28` are faithful, well-parameterized implementations of what the README describes, and several edge cases (date gaps, zero-mean-HR halves in decoupling) are handled with real guards. The problems are in the data flowing into that math and in the fact that the math is implemented twice.

### [Critical] Stream tables have no uniqueness constraint — 46,555 confirmed duplicate rows already in production
- **File:line**: `src/pipeline.py:151-160` (schema — no `PRIMARY KEY`/`UNIQUE`), `:175-189` (`insert_streams` — delete-then-insert, not a DB-enforced upsert)
- **Problem**: **Empirically confirmed by querying the live `.duckdb` file directly**, not inferred:
  ```
  duplicate stream rows (activity_id,time_offset): [(15986640076, 97, 3), (15986640076, 204, 3), ...]
  total activities with duplicate (activity_id,time_offset) in stream_heartrate: 21
  total excess duplicate rows in stream_heartrate: 46555
  ```
  Activity `15986640076` has every one of its 1,094 distinct `time_offset` values stored exactly 3 times. Compare with `upsert_activity`/`upsert_athlete` (lines 162-173), which correctly use `ON CONFLICT ... DO UPDATE` against a real `PRIMARY KEY` — only the seven `stream_*` tables lack this protection.
- **Why it matters**: Duplicated rows inflate `calculate_trimp_edwards`/`calculate_time_in_zones` (rows are counted as 1-second samples, so 3x rows = ~3x inflated load for those 21 activities) and cause `calculate_aerobic_decoupling`'s `pd.merge(..., how='inner')` to multiply further via a many-to-many join.
- **Fix**: Add `PRIMARY KEY (activity_id, time_offset)` to all seven `stream_*` tables; switch `insert_streams` to `INSERT ... ON CONFLICT DO UPDATE`; run a one-time cleanup against the existing file to purge the 46k+ duplicates already present.

### [Critical] Two independent, disagreeing implementations of TRIMP/CTL/ATL/EF
- **File:line**: `src/pipeline.py:294-332` (`run_analyze_effectiveness`) vs. `src/api/main.py:507-554` (`calculate_training_status_logic`)
- **Problem**: The CLI/batch path integrates TRIMP sample-by-sample over the real HR stream; the live dashboard recomputes it a different way, using only the Strava-reported *average* HR. Concrete divergences:
  1. **Sex-default is inverted.** `pipeline.py:313`: unknown sex → *female* coefficient (1.67). `main.py:522`: unknown sex → *male* coefficient (1.92). Same missing data, opposite assumption.
  2. **Efficiency Factor differs by a factor of 60.** `pipeline.py:324`: `ef = (avg_speed * 60) / avg_hr`. `main.py:550`: `average_speed / average_heartrate` — no `*60`. The stored "efficiency_factor" and the dashboard's "daily_ef" are not the same unit for the same activity.
  3. **Activity-type scope differs**: pipeline includes all types; the API filters to `type = 'Run'` only.
- **Why it matters**: The README presents one coherent Banister methodology; in reality there are two, and they show different numbers for identical activities depending on whether you're looking at the CLI output or the web dashboard. A fix in one path never reaches the other.
- **Fix**: Delete one of the two pipelines. Preferably, make the API read from `activity_effectiveness` (populated by `pipeline.py`, which is the more accurate stream-level version) instead of recomputing from summary fields; if the SQL version is kept for performance, fix the `*60` discrepancy and unify the sex-default convention in both places — ideally by factoring the Python fallback logic into `metrics.py` and calling it from both.

### [Critical] Unguarded `sum()` over nullable HR values can crash the batch job and destroy previously computed data
- **File:line**: `src/pipeline.py:296-297` (drops the whole table up front), `:307-311` (`sum(hr_vals) / len(hr_vals)` with no None-filtering, no per-activity try/except)
- **Problem**: Every run starts with `DROP TABLE IF EXISTS activity_effectiveness` then rebuilds from scratch. The per-activity HR average has no null-filtering and no try/except around it. `stream_heartrate.value` is nullable, and Strava does return partial HR dropouts (sensor disconnects) in practice.
- **Why it matters**: A single `NULL` HR sample raises an uncaught `TypeError`, aborting the function partway through — and because the table was dropped up front rather than upserted, every activity processed *after* the crash point loses its previously-computed row too. (Confirmed the current warehouse has zero NULL rows today, so this hasn't fired yet — but nothing in ingestion prevents it, and the blast radius when it does is total.)
- **Fix**: Filter `None` before aggregating; wrap the per-activity body in try/except so one bad activity doesn't abort the batch; stop dropping the whole table up front (upsert per `activity_id`, or populate a staging table and swap).

### [High] No Strava rate-limit/backoff handling anywhere
- **File:line**: `src/pipeline.py:105-121,210-225,241-253`
- **Problem**: Every Strava call does `resp.raise_for_status()`; the only special-cased error string is `"Unauthorized"`. A 429 raises a generic `HTTPError`, which doesn't match, and the pagination loop just `break`s — silently stopping ingestion partway through while still reporting a normal completion.
- **Why it matters**: This app's own usage pattern (293 activities, one streams request each) plausibly exceeds Strava's 100 requests/15-min default limit on a cold backfill or catch-up run, silently truncating ingestion with no visible error.
- **Fix**: Catch 429 specifically, read Strava's `Retry-After` header, sleep, and retry instead of breaking/skipping.

### [High] Web OAuth tokens are captured but never refreshed and never used for ingestion
- **File:line**: `src/api/auth_routes.py:139-176`
- **Problem**: The web login flow stores `strava_access_token`/`strava_refresh_token` on the `User` row, then redirects to `/dashboard` with a comment admitting: *"In a real app we would kick off a background task here to ingest their data."* A repo-wide grep confirms these fields are written once and **never read anywhere else**. The only thing that actually populates the DuckDB warehouse is `pipeline.py`, authenticated via a single `STRAVA_REFRESH_TOKEN` in `.env` — a completely separate, single-tenant credential.
- **Why it matters**: The multi-user `User`/`Race`/`TrainingPlan` schema implies multi-tenant support, but the warehouse all analytics read from is only ever populated for whichever one athlete's token lives in `.env`. Every dashboard endpoint only ever shows that one athlete's data, regardless of who's logged in.
- **Fix**: Either wire `strava-callback` to kick off per-user ingestion using the user's own tokens (with refresh-token handling), or if the app is intentionally single-tenant for now, remove or clearly gate the multi-user registration/OAuth scaffolding so it doesn't look functional when it's a dead end.

### [High] Batch training-load calc silently excludes any activity without a heart-rate stream
- **File:line**: `src/pipeline.py:299` (`INNER JOIN stream_heartrate`)
- **Problem**: Any activity with zero HR-stream rows never appears in the candidate set, so it contributes nothing to `daily_load` — not zero, *invisible*, indistinguishable from a rest day. This is also inconsistent with the API's version, which uses `dim_activity.average_heartrate` directly and doesn't require a stream — so the same activity is excluded from one number and included in the other.
- **Why it matters**: A hard strength session or a run with a failed HR strap connection silently pulls ATL/CTL down as if nothing happened that day.
- **Fix**: Pick one behavior and apply it in both places — fall back to `dim_activity.average_heartrate` when no stream exists (matching the API), or explicitly zero-fill and log which activities were excluded.

### [High] README's pace-zone methodology doesn't match the code, and the code isn't where the README says it is
- **File:line**: `README.md:68` vs. `src/api/main.py:1258-1279`
- **Problem**: README claims "95th percentile of speed over the last 6 weeks." Actual code: a 90-day window, using the 80th percentile of speed (or 90th as a fallback with <30 samples). This logic also lives in `main.py`, not in `metrics.py`/`pipeline.py` as the README's own project-structure section implies.
- **Fix**: Update the README to describe the actual algorithm, and move the logic into `metrics.py` alongside the other methodology functions.

### [Medium] No schema migration tooling for either database
*(Backend review flagged this for Postgres, data review flagged it for DuckDB — same root cause, both stores.)*
- **File:line**: `src/api/database.py:97` (`Base.metadata.create_all(bind=engine)` — Postgres); `src/pipeline.py:128-140` (hand-guarded `ALTER TABLE` wrapped in swallowed exceptions — DuckDB)
- **Problem**: Neither store has a migration framework (`grep` for `alembic` across the repo: zero matches). `create_all` only creates missing tables, never alters existing ones; the DuckDB side requires manually writing and exception-guarding an `ALTER TABLE` for every new column (already needed once, for the `weight` column).
- **Why it matters**: The moment a model gains a column after the table already exists in a deployed DB, nothing errors at startup — the first query touching the new column fails at runtime instead, with no clean migration path.
- **Fix**: Adopt Alembic for the Postgres side; add a minimal schema-version table + ordered migration list for DuckDB, or at minimum a startup check that diffs actual vs. expected columns and fails loudly.

### [Medium] CTL is structurally cold-start biased and can never reach steady state
- **File:line**: `src/pipeline.py:362` (`lookback_days=42`) combined with `src/metrics.py:10-11` (`ctl = [0.0]`)
- **Problem**: A zero-seeded 42-day EWMA only reaches ~63% of steady state after exactly 42 days of *constant* load, and the pipeline only ever ingests 42 days of history — there's no runway for it to converge.
- **Why it matters**: The headline "Fitness" number is systematically understated for every real user, especially right after a backfill.
- **Fix**: Increase `lookback_days` to 3-4x the CTL constant (~120-180 days), and/or seed `ctl[0]`/`atl[0]` from an average of the first few days instead of 0.

### [Medium] `activity_has_streams()` checks only one of seven stream tables, forever
- **File:line**: `src/pipeline.py:192` (hardcoded to `stream_velocity`), used at `:234`
- **Problem**: Any activity genuinely lacking GPS/velocity data (treadmill run, manual entry) fails this check permanently, regardless of how many times HR/other streams have already been successfully fetched.
- **Why it matters**: Wastes Strava API quota re-fetching (and fully re-inserting) data that will never change, on every single future run, for an ever-growing set of no-GPS activities — compounding the rate-limit risk above.
- **Fix**: Check for existence of *any* stream row (or a dedicated "streams processed" flag on `dim_activity`) instead of hardcoding one table.

### [Medium] Stream arrays are zipped without validating equal length — silent truncation
- **File:line**: `src/pipeline.py:179-189`
- **Problem**: `zip()` silently truncates to the shortest input if, e.g., a heartrate stream is shorter than the time stream (sensor connected partway through). **Confirmed in the live DB**: activity `15889717541` has 2,388 rows in `stream_velocity` but 3,134 in `stream_heartrate` for the same activity.
- **Fix**: Assert/log on length mismatch before zipping; pad with `None` to preserve offset alignment, or skip that stream for that activity with a warning, rather than silently truncating.

### [Medium] `ingest_workouts.py` doesn't touch Strava, and destructively replaces its table on every run
- **File:line**: `src/ingest_workouts.py:1-18,156-168,193-195`
- **Problem**: Despite the name, this scrapes `coros.com/training` via Playwright/fragile CSS selectors into `dim_workouts`, and unconditionally `DROP TABLE`+recreates before inserting whatever was scraped — no row-count sanity check.
- **Why it matters**: `main.py` depends on `dim_workouts` for CRUD routes and recommendation logic. If Coros changes their page layout and only a partial scrape succeeds, a complete table is silently replaced with a small partial one, with no error surfaced.
- **Fix**: Rename to reflect what it does (`scrape_coros_workouts.py`); switch to upsert-by-`workout_id`; add a minimum-row-count guard before replacing existing data.

### [Medium] Test coverage misses the one branch production code actually calls
- **File:line**: `src/tests/test_metrics.py` vs. `src/metrics.py:35-36` (`calculate_acwr`'s `pd.Series` branch) vs. `src/api/main.py:596` (calls it with Series)
- **Problem**: `calculate_acwr`'s scalar and list branches are tested; the `pd.Series` branch — the only one actually exercised in production — has no test at all. There's also no test file for `pipeline.py` at all, so none of the Critical/High bugs above are caught by any existing test.
- **Fix**: Add a `pd.Series`-input test for `calculate_acwr` (including a zero-CTL day inside the Series); add a `src/tests/test_pipeline.py` covering the null-HR crash, the aerobic-decoupling edge cases, and a regression test asserting `insert_streams` run twice doesn't duplicate rows.

### [Low] Hardcoded, unpersonalized physiological constants
- **File:line**: `src/pipeline.py:266,310,319`; `src/api/main.py:523,960,1244`
- **Problem**: Resting HR hardcoded to `60` in two places; max-HR fallback is `192` in one place and `190` in three others in `main.py`. `dim_athlete` has no column for resting HR at all.
- **Fix**: Add a `resting_heartrate` column to `dim_athlete` (collected via signup/settings, same pattern as `weight`); centralize the max-HR fallback as one named constant instead of four independent literals.

### [Low] Print-logic bug hides the "no data" case and prints a contradictory success message
- **File:line**: `src/pipeline.py:371-374` — `print(" Failed (no data).")` is indented *inside* `if stats:` with no `else:`, so it always prints right after a success message, and prints nothing at all on genuine failure.
- **Fix**: Move it to an `else:` clause.

### [Low] Unvalidated direct dict-key access on Strava activity payloads
- **File:line**: `src/pipeline.py:229-230` (`act['id']`, `act['name']`, no guard) vs. the athlete-profile fetch a few lines earlier, which correctly uses `.get()` inside a try/except
- **Fix**: Use `.get()` with an explicit skip-and-log for malformed entries, matching the defensive style already used for the athlete fetch.

---

## 3. AI/LLM Integration

**Overall state:** Of the four specific hallucinations the project's own `vp_review3.md`/`vp_review4.md` flagged (calling CTL 14.8 "Optimized," prescribing three hard workouts in one day, nonsensical "reloading energy stores" language, wrong race-distance references), **all four are genuinely fixed** — but only inside the one function (`get_ai_insight`) behind the specific dashboard card that was being screenshotted at the time. The training-plan generator and the plan-editing chat use separate, hand-written prompts that never received the same treatment, because there's no shared prompt-building code for a fix to land in once.

### [Critical] *(See Section 1)* — the unescaped-`innerHTML` XSS sink is the same bug found by both the frontend and AI-integration reviews; full detail is in Section 1, not repeated here.

### [High] Zero authentication on AI-backed endpoints enables cross-user prompt injection and unbounded spend
- **File:line**: `src/api/main.py:1499` (`update_profile`), `:1391` (`update_settings`), `:880` (`/status/{user_id}`), `:1741` (`/api/training-plan/{user_id}/chat`) — none have an auth dependency
- **Problem**: `ProfileUpdate.name` and `SettingsUpdate.race_goal_time` are unvalidated free-text fields, writable for **any** `user_id` with no ownership check, and both flow directly into LLM system prompts (`main.py:1764`, `749-755`).
- **Why it matters**: This is the same root cause as Section 1's "no auth" finding, viewed from the AI angle: an attacker can plant an injection payload into any user's profile/settings fields with no login, then trigger the LLM call for that user — potentially forcing the leading-`<` string that triggers the XSS sink above, or simply running up the app owner's API bill on demand.
- **Fix**: Same as Section 1 — add ownership checks to these routes. Additionally, length-cap and validate free-text fields before they reach a prompt.

### [High] Anti-hallucination guardrails exist in only one of three prompt-construction sites
- **File:line**: `src/api/main.py:754-765` (has guardrails) vs. `:1072-1082` (`get_ai_training_plan`, no guardrails) vs. `:1771-1785` (`training_plan_chat`, no guardrails)
- **Problem**: The guardrails `vp_review4.md` approved are genuinely present, verbatim, in `get_ai_insight`'s system prompt:
  ```
  "Use these rigid scientific benchmarks for context:\n"
  "- CTL: <40 Developing, 40-70 Consistent, 70-100 Advanced, >100 Elite Pro.\n"
  "- TSB: >15 Fresh (Peaking), -30 to -10 Optimal Training, <-30 High Overload.\n"
  "...NEVER contradict the user's current 'Standing'...\n"
  "CRITICAL CONSTRAINTS: 1) Recommending a MAXIMUM of ONE training action for the current day. "
  "2) Do not hallucinate pseudo-science. Running depletes energy. Rest restores it."
  ```
  This backs `/status/{user_id}` and `/recommend/{user_id}` — the exact card the VP reviews were looking at. But `get_ai_training_plan` (the "Your Customized Race Schedule" generator) and `training_plan_chat` (the interactive plan-editing chat) each have independent, hand-written prompts with no CTL/TSB bands and no anti-pseudoscience clause.
- **Why it matters**: The fix patched the symptom that was screenshotted, not the underlying capability — a user chatting with the plan editor can still get ungrounded or CTL-mislabeled advice today.
- **Fix**: Extract the reference bands and constraints into a shared constant/function; include it in all three system prompts (adapting "max one action" to "max one action per day" for the plan generator, which legitimately spans multiple days).

### [High] No shared LLM provider abstraction — logic duplicated 3-4x
*(Found independently by both the backend and AI-integration reviews.)*
- **File:line**: `src/api/main.py:767-874` (`get_ai_insight`), `1084-1193` (`get_ai_training_plan`), `1805-1850` (`training_plan_chat`)
- **Problem**: All three functions independently repeat the same `if provider == "groq": ... elif == "deepseek": ... elif == "gemini": ...` structure with copy-pasted URL/payload/header construction and error handling.
- **Why it matters**: This is precisely why the guardrail fix above didn't propagate, and why only 2 of the 3 call sites currently set a request timeout (see below) — there's no single place to fix any of this once.
- **Fix**: Extract one `call_llm(system_prompt, user_prompt, json_mode=False, timeout=60) -> str` helper, driven by a small `{provider: {url, model, key_env}}` registry, called from all three (and any future) sites.

### [Medium] HR-zone bug fixed at the display layer; the backend aggregation is still unbounded and non-percentage
- **File:line**: `src/api/main.py:1352-1367,1378` (query/response) vs. `src/api/templates/dashboard.html:594-621` (consumer)
- **Problem**: The backend still returns a raw `COUNT(*)` of HR samples across the athlete's **entire history with no date bound** — not a percentage. The percentage conversion, the 0-100 axis cap, and the zero-data guard were all implemented **only in the frontend** (`dashboard.html:595,607,617,596-597`). The specific symptom (Y-axis to 120,000) is genuinely fixed for this one chart, but the underlying endpoint would immediately reproduce it if any other client consumed it, or if the frontend math is ever touched without realizing the backend never normalized.
- **Fix**: Compute the percentage in SQL/Python before returning it from `/analytics/{user_id}`, or at minimum scope the query to a bounded recent window (matching the sibling `load_dist` query's 30-day filter) so raw values can't reach unbounded magnitudes again.

### [Medium] No timeout on `get_ai_insight`'s HTTP calls — indefinite hang risk
*(Found independently by both the backend and AI-integration reviews.)*
- **File:line**: `src/api/main.py:785,814,843,862`
- **Problem**: All four provider branches in `get_ai_insight` call `requests.post(...)` with no `timeout=`, unlike `get_ai_training_plan` and `training_plan_chat`, which both correctly set `timeout=300`.
- **Why it matters**: `get_ai_insight` backs the main dashboard load (`/status/{user_id}`) — a hung upstream provider blocks that request forever, and enough concurrent hangs exhaust FastAPI's thread pool for everyone.
- **Fix**: Add an explicit timeout (20-30s is plenty for a short insight) to all four call sites.

### [Medium] No caching or rate limiting — every page load is a fresh, unbounded paid API call
- **File:line**: `src/api/main.py:894` (`/status/{user_id}` calls `get_ai_insight` on every request, no memoization)
- **Why it matters**: Combined with the missing auth above, any caller can hit this endpoint in a loop and generate unbounded billed usage on the developer's own API keys with no circuit breaker.
- **Fix**: Cache the insight per user for a TTL (e.g. 1 hour, or invalidate on new activity sync); add basic per-user rate limiting on LLM-backed endpoints.

### [Low] `get_ai_insight` can return a raw provider error dict as if it were coaching text
- **File:line**: `src/api/main.py:787-790` (and `816-819,845-848`) — `return str(data)` when the response is HTTP 200 but missing the expected `choices` key
- **Why it matters**: This string then flows into the unsafe `mdToHtml` sink above, potentially rendering malformed/unexpected content as live HTML.
- **Fix**: Replace with a fixed fallback string (`"AI insight temporarily unavailable."`) and log the raw response server-side instead.

### [Low] JSON-parsing tests don't cover prose-wrapped LLM output
- **File:line**: `tests/test_ai.py`, `tests/test_ai_parsing.py` vs. `src/api/main.py:1022-1031` (`clean_json_response`)
- **Problem**: Existing tests correctly validate fence-boundary stripping (```json ... ```), and the function correctly passes all of them. No test covers a model prefacing JSON with prose (`"Here's your plan:\n\`\`\`json..."`), where `startswith`/`endswith` won't match. Partially mitigated in practice by `response_format: {"type": "json_object"}` being set on the actual API calls, and both call sites already catch `JSONDecodeError` gracefully rather than 500ing.
- **Fix**: Add a prose-prefixed test case; consider hardening `clean_json_response` to extract the first `{...}` block via regex rather than relying purely on fence boundaries.

---

## 4. Frontend & UI

**Overall state:** Template inheritance is in better shape than the file sizes suggest — every large template (`dashboard.html`, `training_calendar.html`, `streams.html`, `profile.html`) correctly `{% extends "base.html" %}` and uses blocks. The one holdout, `index.html` (the Activities page), is untemplated legacy HTML, and it's exactly the page with the nav-consistency bug the project's own planning doc already flagged as a to-do. The "Customization is UI theater" complaint from `vp_review3.md` is genuinely improved (the controls do write real state and do flip real CSS) but the implementation is fragmented enough that it silently stops working the moment you navigate to most other pages.

### [High] `index.html` never extends `base.html` — divergent nav header, missing Profile/Logout links
- **File:line**: `src/api/templates/index.html:76-90` vs. `src/api/templates/base.html:12-31`; explicitly flagged as a to-do in `training_calendar_plan.md:8` ("fix the activities page nav header to match the others") and never done
- **Problem**: `index.html` duplicates the entire `<html>/<head>/<header>` shell independently and has zero Jinja syntax. Its hand-copied nav has no `{% if user %}` guard and, critically, no Profile or Logout link at all.
- **Why it matters**: A signed-in user on the Activities page — arguably the most-used data page — has no way to reach Profile or Logout without first clicking back to Dashboard. Any future header change has to be hand-applied here separately and will drift again (it already has).
- **Fix**: Convert to `{% extends "base.html" %}` with `split_styles`/`content` blocks like every other page; move its inline styles into `style.css` or a `split_styles` block.

### [High] "Customization" is functionally wired but inconsistently applied, and partially cosmetic
- **File:line**: `src/api/templates/profile.html:217-221,356-374,629-639`; `dashboard.html:6-24,222-227,504-524`; `training_calendar.html:5-26`; `src/api/static/style.css:21-28`
- **Problem**: There's no modal called "Customization" anymore — the controls were reworked into two cards on `profile.html` (Units, Theme/Layout). Checking each honestly:
  - **Units**: genuinely works — `dashboard.html` and `streams.html` both read `localStorage.units` and change displayed distance/pace. **Gap**: `training_calendar.html` never reads `localStorage` at all (zero matches) and hardcodes miles everywhere.
  - **Theme**: really does set `data-theme="light"` and real `[data-theme="light"]` CSS overrides exist. **Gaps**: (1) the re-apply-on-load script only exists on `dashboard.html`/`profile.html` — `training_calendar.html`, `streams.html`, `index.html`, and all 4 auth pages never re-apply a saved theme, so it silently reverts on 6 of 9 pages; (2) even where applied, `style.css:21-28` sets `body { background: var(--bg-gradient); background-color: var(--bg-color); }`, and the light-mode override only touches `background-color` — which cannot clear a previously-set `background-image`, so the dark gradient stays painted over everything regardless of theme.
  - **Layout**: real, but the compact-layout effect only shows up on the Dashboard page itself, so toggling it on Profile produces zero visible feedback on the page you're looking at — exactly what reads as "did nothing" to anyone testing in place.
- **Fix**: Move the theme-init bootstrap script and the `:root`/`[data-theme]` variable block out of per-page `<style>` blocks and into `base.html`/`style.css` once; make `training_calendar.html` read `localStorage.units`; fix the light-mode `body` rule to clear `background-image` (e.g. use the `background:` shorthand, not `background-color` alone).

### [High] Core design-system CSS variables are undefined in `style.css` and duplicated inconsistently across templates
- **File:line**: `src/api/static/style.css:1-13` vs. `dashboard.html:6-16`, `profile.html:5-22`, `training_calendar.html:5-26`; usages in `base.html:23-25`, `streams.html`, `index.html`
- **Problem**: `style.css`'s `:root` never defines `--text-secondary`, `--text-primary`, `--danger`, `--warning`, `--input-bg`, `--body-bg`, or `--accent-glow` — those are instead redeclared independently (and inconsistently — `training_calendar.html` adds 3 tokens the other two copies lack) inside three templates' own `<style>` blocks. Meanwhile `base.html`'s shared header and `streams.html`/`index.html` all use `var(--text-secondary)`/`var(--text-primary)` a combined 18+ times despite none of those three files defining it and `style.css` not defining it either.
- **Why it matters**: On `streams.html`, `index.html`, and all 4 auth pages, every one of those color rules resolves to nothing and silently falls back to the inherited near-white body color instead of the intended muted gray — flattening the visual hierarchy the design clearly intends (e.g. `streams.html:72,113`'s de-emphasized labels render at full brightness).
- **Fix**: Move the full variable set (dark values + `[data-theme="light"]` overrides) into `style.css` once; delete the three duplicated blocks from individual templates; add the missing tokens so every template resolves the same variables consistently.

### [Medium] No keyboard accessibility anywhere; the training calendar's core interaction is mouse-only
- **File:line**: `training_calendar.html:1268-1284,1339-1342,1367-1370`; `dashboard.html:849-857`
- **Problem**: A sitewide grep for `tabindex`, `aria-`, `keydown`, `role=` across every template returns zero matches. Calendar day cells and workout items are plain non-focusable `<div>`s with only a `click` handler — the only way to open the add/view-workout modal (the entire point of the page) is a mouse click.
- **Fix**: Add `tabindex="0"`/`role="button"` and `keydown` handlers (Enter/Space) mirroring the click handlers; add `aria-label` to icon-only buttons; add modal semantics (`role="dialog"`, focus-move on open, Escape-to-close).

### [Medium] Drag-and-drop on the dashboard's schedule is cosmetic only — another instance of "UI theater"
- **File:line**: `dashboard.html:849-857`
- **Problem**: `dragstart` calls `e.dataTransfer.setData(...)`, but `drop` never calls `getData(...)` — nothing reorders the schedule or persists anything. The only visible effect of dropping a card is a border-color flash.
- **Fix**: Either wire `drop` to actually reorder/persist the plan, or remove `draggable="true"` and the handlers until it's implemented.

### [Medium] Empty/loading states are well-designed in ~3 places and ad hoc everywhere else
- **File:line**: `dashboard.html:528-543` (good, reusable `showEmptyState()` with icon+CTA, correctly used for the PMC/HR-zone/training-load charts) vs. `dashboard.html:478-502` (Pace Zones table has no failure branch at all — stuck on "Loading..." forever on error) and `profile.html:625` (`catch (e) { /* silent */ }` — stats stay on `"—"` forever with no explanation) and `streams.html:408` (bare `'--'` with zero messaging)
- **Why it matters**: The specific fix `vp_review3.md` praised (icon + CTA empty states, HR-zone garbage-data guard) is real, but scoped to 3 of roughly 10 data cards across the app — the rest still show exactly the pattern that review asked to eliminate.
- **Fix**: Move `showEmptyState()`/its CSS into `style.css` so every template can reuse it; add proper empty/error branches to the pace-zones, profile-stats, and streams loaders.

### [Medium] Broken/missing asset references render as invisible blanks
- **File:line**: `dashboard.html:251,861` (Font Awesome icon classes; Font Awesome is never loaded anywhere in the app — no `<link>`/`<script>` include exists); `index.html:263,319,405` (`.loader` div; `.loader` is never defined in CSS, only an unrelated `.loader-spinner` scoped inside `training_calendar.html`)
- **Fix**: Add the Font Awesome CDN link to `base.html` (or replace with the inline-SVG icon style already used elsewhere in the app); add a `.loader` rule to `style.css` or swap in `.loader-spinner`.

### [Medium] Heavy JS/CSS duplication; no shared JS file exists at all
- **File:line**: `src/api/static/` contains only `style.css`. 34 total `fetch()` call sites across templates, each with its own copy of error handling; Chart.js is loaded via unpinned CDN URL independently in `streams.html` and `dashboard.html` with duplicated, slightly-different global-defaults setup; an identical `@keyframes fade-up` + date-picker CSS rule is copy-pasted verbatim across all 4 auth templates.
- **Why it matters**: Every bugfix or style tweak currently has to be hand-applied in 4-6 places and reliably drifts, exactly as seen in the CSS-variable and empty-state findings above.
- **Fix**: Add `src/api/static/app.js` for shared helpers (`fetchJSON`, `formatDistance`/`formatPace`, `showToast`, unit conversions) included from `base.html`; move the duplicated `@keyframes`/CSS rules into `style.css`; pin and load Chart.js once.

### [Low] No active/current-page highlighting in the nav, despite being an explicit requirement
- **File:line**: `base.html:22-29`; required by `training_calendar_plan.md:8`
- **Fix**: Pass the current route into the template context and add an `active` class check on each nav link.

### [Low] Low-opacity muted text/icons likely fail WCAG AA contrast
- **File:line**: `style.css:253-263` (`.info-icon` at `opacity: 0.6`); `streams.html:116-121` (`.info-trigger` at `opacity: 0.5`)
- **Problem**: `#94a3b8` at full opacity against the app's dark background is ≈7.5:1 (fine), but blended at 0.5-0.6 opacity it drops to ≈3.3-3.4:1 — below the 4.5:1 minimum for normal-size text, and these interactive info-glyphs are ~12px.
- **Fix**: Use a dedicated, less-muted solid color verified at 4.5:1+ instead of an opacity trick; reserve opacity/brightness changes for `:hover`.

---

## 5. DevOps, Testing & CI

**Overall state:** There is no CI of any kind, dependencies are completely unpinned and have already drifted from what's actually installed (breaking test collection today), and a dead entry point sits in the tree importing modules that don't exist. The most urgent item here isn't code quality — it's that running the test suite as-is makes real, billed network calls.

### [Critical] `pytest` collection makes live, unmocked, billed calls to third-party LLM APIs
- **File:line**: `test_gemini.py:41` (top-level module code, no function, no `__main__` guard); `test_groq.py:25` (same pattern)
- **Problem**: **Empirically confirmed, not inferred** — running `python -m pytest test_gemini.py --collect-only` produces `collected 0 items` after a genuine 34.78-second stall, because pytest's *import* of the file (to check for tests) executes the live `requests.post(...)` to Gemini's real API. Both files have confirmed non-empty real API keys loaded from `.env`/`.env.dev`. `test_deepseek.py` is collection-safe (wrapped in a function + `__main__` guard) but contains zero `assert` statements and swallows all exceptions into `print()` calls — if it were ever run as a real test, it could never fail regardless of what the API returns.
- **Why it matters**: Anyone (or any future CI) running bare `pytest` from repo root silently burns real API quota/cost and can hang for the full request timeout, non-deterministically.
- **Fix**: Rename these out of the `test_*.py` pattern entirely (e.g. `scripts/manual_check_gemini.py`) so pytest never imports them, or rewrite as real tests with the HTTP layer mocked (`unittest.mock`/`respx`).

### [High] `requirements.txt` has zero version pins, and the installed venv has already drifted from it
- **File:line**: `requirements.txt:1-15` (no `==` anywhere in the file); confirmed installed packages via `pip list` show no `psycopg2` at all despite it being listed
- **Problem**: **Empirically confirmed** — `pytest --collect-only` currently hard-fails on `tests/test_ai.py` and `tests/test_ai_parsing.py` with `ModuleNotFoundError: No module named 'psycopg2'`, tracing through `src/api/database.py:8`. Two of four test modules cannot even be collected in the current dev environment.
- **Fix**: `pip install -r requirements.txt` (or add `psycopg2-binary` directly) to fix the immediate breakage; then run `pip freeze` and hand-trim to `==`-pinned direct dependencies so this can't silently drift again.

### [High] `run.py` is dead code — imports 4 modules that don't exist anywhere in the repo
- **File:line**: `run.py:1-5` — `from src.fetch_strava_data import StravaManager`, `from src.ingest_strava_duckdb import main as ingest`, `from src.analyze_effectiveness import analyze_effectiveness`, `from src.analyze_training_load import calculate_training_load`
- **Problem**: **Confirmed** — none of these files exist (tracked or untracked); directly running `python3 -c "from src.fetch_strava_data import StravaManager"` raises `ModuleNotFoundError`. This functionality was fully reimplemented as a single self-contained file, `src/pipeline.py`, which is what the README actually instructs users to run.
- **Fix**: Delete `run.py` (superseded by `src/pipeline.py`).

### [Medium] Test suite fragmented across 3 locations, no pytest config, with duplicated test files
- **File:line**: `src/tests/test_metrics.py` (real, solid, no network) vs. root-level `tests/` package vs. 3 non-test scripts at repo root; no `pytest.ini`/`pyproject.toml`/`conftest.py` anywhere
- **Problem**: `tests/test_ai.py` and `tests/test_ai_parsing.py` are near-duplicates of each other (same 5 test cases, one written pytest-style, one `unittest`-style) — a change to the function they test requires updating assertions in two places that will silently drift.
- **Fix**: Consolidate into one `tests/` directory at repo root; add `pyproject.toml` with `testpaths = ["tests"]`; delete the duplicate test file; move the 3 manual scripts out of the `test_*` naming convention.

### [Medium] No CI configuration exists anywhere
- **File:line**: no `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, or equivalent found
- **Fix**: Add a minimal GitHub Actions workflow running `pip install -r requirements.txt && pytest tests` — but only *after* the live-API-call and unpinned-dependency findings above are fixed, or CI will burn API credits/hang on every push.

### [Medium] No `.dockerignore` — the root `Dockerfile`'s `COPY . .` bakes the 66MB personal-data file into the image
- **File:line**: `Dockerfile:15`; no `.dockerignore` exists
- **Why it matters**: This is a second, independent distribution channel for the same personal Strava data flagged at the top of this document — if this image is ever pushed to a registry, the data ships there too.
- **Fix**: Add a `.dockerignore` (`data/`, `.git/`, `venv/`, `*.duckdb`, `.env*`) — combined with untracking the file from git, this closes both channels.

### [Low] Two Dockerfiles, genuinely different but undocumented
- **File:line**: `Dockerfile` (root — Render-oriented, `COPY . .`, reads `$PORT`) vs. `src/api/Dockerfile` (used by `docker-compose.yml`, bakes in `--reload`)
- **Problem**: Not accidental duplication, but nothing documents the split, so a future editor has a coin-flip chance of editing the one that isn't actually used by `docker-compose up`.
- **Fix**: Add a one-line note in the README explaining which Dockerfile is for what.

### [Low] Both Dockerfiles (and the local dev venv) pin to Python 3.9, which is past end-of-life
- **File:line**: `Dockerfile:1`, `src/api/Dockerfile:1` (`FROM python:3.9-slim`)
- **Fix**: Bump to a supported version (3.11/3.12) in both Dockerfiles and the local venv; re-run the test suite to catch version-specific breakage.

### [Medium] `lm_studio_proxy.py` returns the wrong response type and is unused/untested
- **File:line**: `lm_studio_proxy.py:15-23`
- **Problem**: Returns a raw `httpx.Response` instead of a Starlette `Response` (FastAPI will try to JSON-encode it rather than proxy the body), and the `async with httpx.AsyncClient()` block closes the connection before a streamed body could be read. It's also not referenced by `docker-compose.yml`, `README.md`, or any other module in the app.
- **Fix**: Wrap the body in `StreamingResponse(response.aiter_bytes(), ...)` and keep the client alive for the stream's lifetime — or delete the file if it's a superseded experiment now that Groq/DeepSeek/Gemini are the active providers.

---

## 6. Backend Architecture (cross-cutting)

### [High] `src/api/main.py` is a single 1,952-line file mixing 7+ unrelated concerns
- **File:line**: whole file. Rough breakdown: `1-62` setup/middleware, `64-480` page routes + activity/workout CRUD with inline raw SQL, `507-670` a 164-line CTL/ATL/TSB/ACWR/VO2 metrics engine living as a bare function (not a route), `706-878` LLM prompt-building fused with a 4-provider HTTP client, `880-1329` more routes + pace-zone math, `1331-1936` races/profile/training-plan CRUD and a second and third copy of the LLM client.
- **Why it matters**: This is the root cause behind most of the duplication findings elsewhere in this document (3x LLM provider logic, repeated DB-connection boilerplate, an unused auth helper that already exists in `auth_routes.py` but is never imported here). It also makes the metrics logic impossible to unit-test independently of FastAPI.
- **Fix**: Split into `routers/activities.py`, `routers/training_plan.py`, `routers/races.py`, etc.; `services/metrics.py` for the CTL/ATL/TSB/VO2/pace-zone calculations; `services/llm.py` for the provider-calling logic; `schemas.py` for the Pydantic models currently scattered across the file. Do this **after** the security fixes in Section 1 land (see Section 7) so the auth dependency gets added once, correctly, rather than being copy-pasted into new module boundaries.

### [Medium] Bare `except:` blocks swallow all exceptions, including unrelated bugs
- **File:line**: `main.py:215,494,961,1245`; `auth_routes.py:111`
- **Problem**: E.g. `try: users = con.execute(...).fetchall() except: return []` — this swallows *any* exception (bad query, connection failure) and silently returns empty, making a real bug indistinguishable from "no data."
- **Fix**: Catch specific exceptions (e.g. `duckdb.CatalogException`) and log unexpected ones instead of a bare `except: pass`/`return []`.

### [Medium] Internal exception text is routinely leaked to API clients
- **File:line**: dozens of sites, e.g. `main.py:62,128,163,254,269,305,347,412,435,453,467,478,503` — the dominant pattern is `except Exception as e: raise HTTPException(status_code=500, detail=str(e))`
- **Why it matters**: `detail` is serialized straight into the JSON response, so raw exception text (SQL fragments, file paths, library internals) is exposed to any caller — which matters more than usual given Section 1's finding that most of these routes have no auth at all.
- **Fix**: Log the full exception server-side; return a generic message for 5xx responses.

### [High] `/api/mileage-summary/{user_id}` queries Postgres-only tables through the DuckDB connection — unconditionally broken
- **File:line**: `main.py:179,202-206,227-232`
- **Problem**: Opens both a DuckDB connection (`con`) and a Postgres session (`db`), correctly uses `db` for the `User` lookup, but then queries `TrainingPlan`/`TrainingPlanEntry` (SQLAlchemy models that live in Postgres) through `con` (DuckDB) — DuckDB has no such tables, so this raises a Catalog Error on every call.
- **Fix**: Replace the raw `con.execute(...)` calls with SQLAlchemy queries against the already-open `db` session, mirroring the pattern already used correctly in `get_training_plan` (`main.py:1608+`).

### [Medium] `delete_activity` reports success even when every delete fails
- **File:line**: `main.py:395-414`
- **Problem**: Per-table delete failures are caught, printed, and ignored; the function unconditionally returns `{"message": "Activity deleted successfully"}` regardless of whether anything was actually deleted.
- **Fix**: Track whether any delete affected rows; return an error/partial-failure status if all deletes failed.

### [Medium] Repeated DB boilerplate; an existing auth helper is never reused
- **File:line**: `main.py` — `get_db_connection()`/`con.close()` copy-pasted ~25 times; the cookie-check logic re-implemented inline 6 times despite `auth_routes.py:31-35` already defining a reusable `get_current_user_from_cookie(request, db)` that `main.py` never imports
- **Fix**: Import and reuse the existing helper; introduce a `Depends`-based DuckDB connection dependency to eliminate the manual `try/finally: con.close()` boilerplate everywhere.

### [Low] F-string-built SQL passed to DuckDB — not currently exploitable, but a risky pattern to leave in place
- **File:line**: `main.py:220,407,537-542` — all current inputs are internal constants, not user data, but the *shape* (f-string into SQL text) is identical to what a real injection looks like, and it's inconsistent with the parameterized style used everywhere else in the file.
- **Fix**: Bind values as `?` parameters instead of interpolating; where a table name must be an f-string (e.g. the `DELETE FROM {table}` loop), add an explicit `assert table in ALLOWED_TABLES` so the safety is enforced in code, not just by convention.

---

## 7. Suggested Execution Order

Fixing these in the wrong order means redoing work — e.g., refactoring `main.py` into modules before adding auth means adding auth to new module boundaries instead of once, and polishing the AI prompts before fixing the underlying data duplication means polishing output derived from corrupted input. Suggested phasing:

**Phase 0 — Stop the bleeding (do first, touches almost no application logic):**
1. Resolve the git/GitHub data exposure (Immediate Action section at the top) — untrack the `.duckdb` file and `.env.dev`, decide on history rewrite, rotate the Strava token if warranted.
2. Add `.dockerignore`.
3. Neutralize the live-API-call test scripts (Section 5) so `pytest`/CI can run safely.
4. Fix the unescaped-`innerHTML` XSS sink (Section 1/3) — small, isolated, high-value.

**Phase 1 — Identity and access control (Section 1):**
5. Add a real auth dependency (signed session, password hashing) and apply it to every user-scoped route.
6. Fix CORS policy; add CSRF protection; fix the reflected-XSS callback bug.

**Phase 2 — Data integrity (Section 2):**
7. Add uniqueness constraints to stream tables and dedupe the existing 46k+ rows.
8. Fix the null-HR crash and the drop-then-rebuild data-loss pattern in `pipeline.py`.
9. Reconcile the two disagreeing TRIMP/CTL/ATL/EF implementations into one shared module.
10. Add Strava rate-limit/backoff handling; decide the fate of the dead multi-tenant OAuth scaffolding.

**Phase 3 — Backend architecture cleanup (Section 6):**
11. Split `main.py` into routers/services/schemas now that auth and data-layer fixes exist to carry forward cleanly.
12. Extract the shared LLM provider-calling helper (unblocks Phase 4).
13. Add migration tooling for both databases; fix the wrong-DB-query and false-success bugs.

**Phase 4 — AI polish (Section 3):**
14. Propagate the anti-hallucination guardrails to all three prompt sites via the new shared helper.
15. Add timeouts, caching, and rate limiting to LLM calls.

**Phase 5 — Frontend consistency & accessibility (Section 4):**
16. Fix `index.html`'s template inheritance and nav.
17. Consolidate CSS variables into `style.css`; fix the light-theme background bug; propagate theme-init to every page.
18. Add a shared `app.js`; fix empty states everywhere; add keyboard accessibility; wire up or remove the cosmetic drag-and-drop.

**Phase 6 — Remaining DevOps/testing polish (Section 5):**
19. Pin `requirements.txt`; consolidate test directories; add a pytest config and a CI workflow (only after Phase 0's step 3 makes this safe); bump Python version; remove/document dead files (`run.py`, `lm_studio_proxy.py`).

---

## Methodology note

Findings above came from five focused review passes (backend/auth, data pipeline/metrics, AI/LLM integration, frontend/templates, DevOps/repo hygiene), each given the relevant files to read in full plus this project's own historical self-review documents (`agent_prompting/vp_review*.md`, `eng_plan.md`, `qa_review.md`) so that claims of "this was already fixed" could be checked against current code rather than taken on faith. A subset of findings — marked explicitly in the text above — were verified empirically (querying the live `.duckdb` file directly, running `git ls-remote`/`git log`, actually running `pytest --collect-only` and observing a real network stall) rather than by code inspection alone; those are facts, not inferences, and should be treated with the highest confidence in this document.
