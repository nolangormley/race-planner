"""The flows a user actually goes through, in a real browser against the
real app, fake Strava and a fresh database. Tests run in file order: the
sync test fills the warehouse that the page tests then look at."""
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
from playwright.sync_api import expect

from e2e.conftest import login


def test_sign_up_leads_to_a_first_race_and_the_strava_prompt(app_server, page):
    stamp = int(time.time())
    page.goto(f"{app_server}/register")
    page.fill("#name", "Sign Up Tester")
    page.fill("#email", f"signup{stamp}@example.com")
    page.fill("#password", "another-password-1")
    page.fill("#date_of_birth", "1992-02-03")
    page.select_option("#gender", "Female")
    page.fill("#height-ft", "5")
    page.fill("#height-in", "6")
    page.fill("#weight-input", "130")
    page.click("button[type=submit]")
    page.wait_for_url("**/setup-race")

    page.select_option("#race_length", "10k")
    page.fill("#race_date", "2027-04-10")
    page.click("button[type=submit]")
    page.wait_for_url("**/strava-auth")
    assert "Authorize with Strava" in page.content()

    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    assert "10k" in page.text_content("#races")
    assert page.text_content("#view-height").strip().startswith("5′ 6″")


def test_dashboard_before_any_sync_shows_empty_states(app_server, registered_user, page):
    login(page, app_server)
    page.wait_for_load_state("networkidle")
    assert "No training history yet" in page.text_content("#pmc-chart")
    assert "Never synced" in page.text_content("#sync-status")


def test_sync_button_runs_a_sync_with_progress_and_fills_the_dashboard(app_server, registered_user, page):
    login(page, app_server)
    page.wait_for_load_state("networkidle")
    page.click("#sync-btn")
    page.wait_for_selector(".sync-panel", timeout=15000)
    page.wait_for_selector(".sync-panel .progress.is-done", timeout=120000)
    assert "3 new activities" in page.text_content(".sync-panel .detail")

    # The page reloads its own data on the strava:synced event; no reload needed.
    page.wait_for_function("document.querySelector('#stat-fitness .stat-value').textContent.trim() !== '–'", timeout=30000)
    assert "Synced" in page.text_content("#sync-status")
    # Each card reloads from its own request, so wait for each one instead of counting once.
    page.locator("#pmc-chart svg path").first.wait_for()
    expect(page.locator("#pmc-chart .empty")).to_have_count(0)            # the empty state from before the sync is gone
    expect(page.locator("#hr-chart svg .cat-label")).to_have_count(5)     # five zone rows, bars only where time was spent
    page.locator("#hr-chart svg .bar").nth(1).wait_for()
    expect(page.locator("#recent tbody tr")).to_have_count(3)
    page.locator("#predictions tbody tr").nth(3).wait_for()
    page.locator("#volume-chart svg .bar").nth(2).wait_for()


def test_every_page_renders_without_errors(app_server, registered_user, page):
    login(page, app_server)
    activity_id = requests.get(f"{app_server}/api/activities", cookies=page.context.cookies and {
        cookie["name"]: cookie["value"] for cookie in page.context.cookies()
    }).json()[0]["activity_id"]
    for path, marker in [
        ("/training-calendar", "#cal-grid .cal-cell"),
        ("/warehouse", "#activities-body tr.link"),
        (f"/workout_streams/{activity_id}", "#streams-chart svg path"),
        ("/profile", "#view-max-hr"),
    ]:
        page.goto(f"{app_server}{path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(marker, timeout=15000)
    page.wait_for_function("document.querySelector('#view-max-hr').textContent.includes('bpm')")
    assert "185 bpm" in page.text_content("#view-max-hr")   # highest heart rate in the synthetic runs


def test_calendar_entry_can_be_added_edited_and_removed(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/training-calendar")
    page.wait_for_load_state("networkidle")
    # A day without a synced run: clicking a run's chip opens the activity instead.
    cell = page.locator(".cal-cell:not(.other):not(:has(.chip.activity))").nth(20)
    date_key = cell.get_attribute("data-date")
    cell.click()
    page.wait_for_selector("#entry-modal.open")
    page.select_option("#entry-type", "Tempo")
    page.fill("#entry-distance", "7 miles")
    page.click("#entry-save")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    chip = page.locator(f'.cal-cell[data-date="{date_key}"] .chip:not(.activity)')
    chip.wait_for()
    assert "Tempo" in chip.text_content()

    chip.click()
    page.wait_for_selector("#entry-modal.open")
    page.check("#entry-completed")
    page.click("#entry-save")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    page.wait_for_selector(f'.cal-cell[data-date="{date_key}"] .chip.done')

    page.once("dialog", lambda dialog: dialog.accept())
    chip.click()
    page.wait_for_selector("#entry-modal.open")
    page.click("#entry-delete")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    selector = f'.cal-cell[data-date="{date_key}"] .chip:not(.activity)'
    page.wait_for_function("selector => document.querySelector(selector) === null", arg=selector)


def test_changing_heart_rate_profile_recomputes_metrics(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    page.click("#edit-athlete")
    page.fill("#f-rest-hr", "55")
    page.fill("#f-max-hr", "190")
    page.click("#athlete-form button[type=submit]")
    page.wait_for_selector(".sync-panel .progress.is-done", timeout=60000)
    assert "Metrics updated" in page.text_content(".sync-panel .label")
    assert page.text_content("#view-max-hr").strip() == "190 bpm"


def test_saving_other_athlete_details_leaves_heart_rate_alone(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    page.click("#edit-athlete")                                       # enabled once the heart rate profile is in
    with page.expect_request(lambda request: request.method == "POST" and "/profile/" in request.url) as saved:
        page.click("#athlete-form button[type=submit]")
    body = saved.value.post_data_json
    assert "resting_hr" not in body and "max_hr" not in body          # a null here would reset them and recompute
    expect(page.locator("#toast")).to_have_text("Profile saved")
    assert page.text_content("#view-rest-hr").strip() == "55 bpm"


def test_athlete_form_waits_for_the_heart_rate_profile(app_server, registered_user, page):
    login(page, app_server)
    held = []
    page.route("**/api/hr-profile/*", lambda route: held.append(route))
    page.goto(f"{app_server}/profile")
    expect(page.locator("#edit-athlete")).to_be_disabled()
    assert held, "the page did not ask for the heart rate profile"
    held[0].continue_()
    expect(page.locator("#edit-athlete")).to_be_enabled()

    # Without the profile the rest of the form opens, but the heart rates stay locked.
    page.unroute("**/api/hr-profile/*")
    page.route("**/api/hr-profile/*", lambda route: route.fulfill(status=404, json={"detail": "Not found"}))
    page.reload()
    page.click("#edit-athlete")
    expect(page.locator("#f-rest-hr")).to_be_disabled()
    expect(page.locator("#f-max-hr")).to_be_disabled()
    expect(page.locator("#max-hr-hint")).to_contain_text("did not load")


def test_max_heart_rate_with_none_recorded_reads_as_the_default(app_server, registered_user, page):
    login(page, app_server)
    page.route("**/api/hr-profile/*", lambda route: route.fulfill(json={
        "resting_hr": None, "max_hr": None, "default_resting_hr": 60,
        "coros_resting_hr": None, "detected_max_hr": None, "default_max_hr": 190,
    }))
    page.goto(f"{app_server}/profile")
    expect(page.locator("#view-max-hr")).to_have_text("190 bpm (default)")


FINISHED_RECOMPUTE = {
    "status": "done", "kind": "recompute", "phase": "done", "label": "Metrics updated", "detail": "",
    "done": 0, "total": 0, "percent": 100, "new_activities": None, "error": None, "error_kind": None,
}


def test_a_recompute_already_done_at_the_first_read_is_still_announced(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    saved = []

    def save(route):
        if route.request.method != "POST":
            return route.continue_()
        saved.append(route.request.post_data_json)
        route.fulfill(json={"message": "Profile updated", "recompute_started": True})

    def job_state(route):
        if not saved:
            return route.continue_()
        route.fulfill(json={"job": FINISHED_RECOMPUTE, "last_synced_at": None})

    page.route(re.compile(r".*/profile/\d+$"), save)
    page.route(re.compile(r".*/api/sync-strava/\d+$"), job_state)
    page.click("#edit-athlete")
    page.click("#athlete-form button[type=submit]")
    page.wait_for_selector(".sync-panel .progress.is-done")
    assert "Metrics updated" in page.text_content(".sync-panel .label")


def test_build_plan_fills_the_calendar_to_race_day(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    race_day = date.today() + timedelta(weeks=10)
    response = page.request.post(f"{app_server}/api/races/{user_id}", data={
        "name": "Spring Half", "race_length": "half marathon", "race_date": race_day.isoformat(),
    })
    assert response.ok, response.text()

    page.goto(f"{app_server}/training-calendar")
    page.wait_for_load_state("networkidle")
    page.click("#cal-build")
    page.wait_for_selector("#build-modal.open")
    page.wait_for_function("document.querySelector('#build-race option[value]:not([value=\"\"])') !== null")
    assert "Spring Half" in page.text_content("#build-race")
    page.fill("#build-weekly", "25")
    page.select_option("#build-days", "4")
    page.click("#build-save")
    page.locator("#build-modal.open").wait_for(state="hidden")
    page.wait_for_selector("#toast.show")
    assert "weeks" in page.text_content("#toast")

    today = date.today()
    months_ahead = (race_day.year - today.year) * 12 + race_day.month - today.month
    for _ in range(months_ahead):
        page.click("#cal-next")
    race_chip = page.locator(f'.cal-cell[data-date="{race_day.isoformat()}"] .chip')
    race_chip.wait_for()
    assert "Race" in race_chip.text_content()
    page.click("#cal-prev")
    page.locator(".cal-cell:not(.other) .chip").nth(27).wait_for()   # the month before the race is planned day by day


def test_planned_race_sits_inside_the_fitness_chart(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    label = page.locator("#pmc-chart .marker-label", has_text="Spring Half")
    label.wait_for()
    chart, box = page.locator("#pmc-chart svg").bounding_box(), label.bounding_box()
    assert chart["x"] <= box["x"] and box["x"] + box["width"] <= chart["x"] + chart["width"], (chart, box)
    assert box["y"] >= chart["y"], (chart, box)

    # The solid lines stop today, so the right edge carries the planned values on race day.
    race_day = page.request.get(f"{app_server}/api/projection/{user_id}").json()["days"][-1]
    expected = sorted(str(math.floor(race_day[key] + 0.5)) for key in ("ctl", "atl"))
    expect(page.locator("#pmc-chart .end-label")).to_have_count(2)
    assert sorted(page.locator("#pmc-chart .end-label").all_text_contents()) == expected


def test_weekly_digest_can_be_previewed_and_switched_on(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    assert not page.is_checked("#digest-enabled")
    page.click("#digest-preview")
    page.wait_for_function("document.querySelector('#digest-body').textContent.startsWith('Subject:')")
    preview = page.text_content("#digest-body")
    assert "Your week in running" in preview and "Last week" in preview

    page.check("#digest-enabled")
    page.wait_for_selector("#toast.show")
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.is_checked("#digest-enabled")


def test_profile_survives_digest_settings_that_do_not_load(app_server, registered_user, page):
    login(page, app_server)
    page.route(re.compile(r".*/api/digest/\d+$"), lambda route: route.fulfill(status=404, json={"detail": "Not found"})
               if route.request.method == "GET" else route.continue_())
    page.goto(f"{app_server}/profile")
    expect(page.locator("#digest-hint")).to_contain_text("did not load")


def test_coros_link_pulls_nights_and_fills_the_recovery_card(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    page.fill("#coros-email", "coros@example.com")
    page.fill("#coros-password", "coros-pass")
    page.click("#coros-link")
    page.wait_for_selector("#coros-linked:not(.hidden)")
    assert "14 nights" in page.text_content("#coros-nights")
    assert "bpm" in page.text_content("#coros-rhr")

    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#recovery-tiles .recovery-tile")
    tiles = page.text_content("#recovery-tiles")
    assert "ms" in tiles and "bpm" in tiles
    page.wait_for_selector("#recovery-chart svg")
    page.wait_for_selector("#readiness .badge")
    assert page.text_content("#readiness .badge").startswith("Today:")
    page.wait_for_selector("#energy-chart svg path")
    expect(page.locator("#energy-value")).to_have_text(re.compile(r"^\d{1,3}/ 100$"))


def test_pacing_page_builds_splits_for_a_race_with_a_goal(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    response = page.request.post(f"{app_server}/api/races/{user_id}", data={
        "name": "Pacing Half", "race_length": "half marathon",
        "race_date": (date.today() + timedelta(weeks=8)).isoformat(), "goal_time": "1:45:00",
    })
    assert response.ok, response.text()
    race_id = response.json()["id"]

    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    page.click(f'a[href="/races/{race_id}/pacing"]')
    page.wait_for_url(f"**/races/{race_id}/pacing")
    page.wait_for_selector("#splits-table tbody tr")
    rows = page.locator("#splits-table tbody tr")
    assert rows.count() == 14                                   # 13 miles and the last 0.11
    assert "1:45:00" in rows.nth(13).text_content()
    assert "1:45:00" in page.input_value("#goal")
    even_first_row = rows.nth(0).text_content()

    page.click('#strategy button[data-strategy="negative"]')
    page.wait_for_function(
        "text => document.querySelector('#splits-table tbody tr').textContent !== text", arg=even_first_row,
    )
    assert "1:45:00" in rows.nth(13).text_content()             # the finish time does not move
    assert page.locator("#pace-band tr").count() == 14


def wait_for_idle_sync(page, app_server, user_id, seconds=60):
    """The last sync job once nothing is running (the COROS link above may
    have started a recompute)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = page.request.get(f"{app_server}/api/sync-strava/{user_id}").json()["job"]
        if not job or job["status"] != "running":
            return job
        time.sleep(0.5)
    raise AssertionError("a sync was still running")


def test_activity_page_shows_what_the_structure_pass_found(app_server, registered_user, page):
    login(page, app_server)
    run = page.request.get(f"{app_server}/api/activities").json()[0]
    assert run["structure_checked"]                                   # the sync ran the pass
    structure = page.request.get(f"{app_server}/api/activities/{run['activity_id']}/intervals").json()
    # The fake runs hold one ten-minute faster stretch: too short for a
    # tempo and too few reps for intervals, so the card stays hidden.
    assert structure["detected"] and structure["kind"] is None and structure["reps"] == []
    page.goto(f"{app_server}/workout_streams/{run['activity_id']}")
    page.wait_for_selector("#streams-chart svg path")
    page.wait_for_load_state("networkidle")
    assert "hidden" in page.get_attribute("#structure-card", "class")
    assert page.locator("#streams-chart .x-band").count() == 0


def test_a_ride_shows_speed_splits_and_pedal_cadence(app_server, registered_user, page):
    login(page, app_server)
    run = page.request.get(f"{app_server}/api/activities").json()[0]

    def as_ride(route):
        response = route.fetch()
        route.fulfill(response=response, json={**response.json(), "type": "Ride"})

    page.route(f"**/api/activities/{run['activity_id']}/effectiveness", as_ride)
    page.goto(f"{app_server}/workout_streams/{run['activity_id']}")
    page.wait_for_selector("#splits-card:not(.hidden)")
    assert "Speed" in page.text_content("#splits thead")
    expect(page.locator("#splits tbody td", has_text="mph vs avg").first).to_be_visible()   # the delta in the column's unit
    assert page.locator("#splits tbody tr").first.locator("td").nth(6).text_content().strip() == "85"
    expect(page.locator("#stat-cadence")).to_have_text("85rpm")
    expect(page.locator("#streams-chart .panel-title", has_text="Cadence (rpm)")).to_have_count(1)


def test_a_sync_marks_the_planned_run_it_matches_done(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    latest = page.request.get(f"{app_server}/api/activities").json()[0]
    created = page.request.post(f"{app_server}/api/training-plan/{user_id}/entry", data={
        "date": latest["start_date_local"][:10], "workout_type": "Easy Run", "approximate_distance": "5 miles",
    })
    assert created.ok, created.text()

    wait_for_idle_sync(page, app_server, user_id)
    started = page.request.post(f"{app_server}/api/sync-strava/{user_id}")
    assert started.status == 202, started.text()
    job = wait_for_idle_sync(page, app_server, user_id)
    assert job["status"] == "done", job

    entries = page.request.get(f"{app_server}/api/training-plan/{user_id}").json()["entries"]
    entry = next(item for item in entries if item["id"] == created.json()["id"])
    assert entry["is_completed"]
    assert entry["strava_activity_id"] == latest["activity_id"]          # an 11-digit Strava id


def gpx_track(start, name, points=400, spacing_s=3):
    """A straight 3.2 m/s run north with heart rate, as a GPX document."""
    rows = []
    for index in range(points):
        moment = start + timedelta(seconds=index * spacing_s)
        rows.append(
            f'<trkpt lat="{40.44 + index * 0.0000863:.7f}" lon="-79.9959"><time>{moment.strftime("%Y-%m-%dT%H:%M:%SZ")}</time>'
            f'<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{148 + index % 6}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>'
        )
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            f'<trk><name>{name}</name><type>running</type><trkseg>{"".join(rows)}</trkseg></trk></gpx>')


def test_a_runner_without_strava_imports_a_gpx_file(app_server, page, tmp_path):
    user = {"email": f"files{int(time.time())}@example.com", "password": "file-runner-123", "name": "File Runner"}
    response = requests.post(f"{app_server}/register", data={
        **user, "date_of_birth": "1991-03-04", "gender": "Female", "height": "1.65", "weight": "58",
    }, allow_redirects=False)
    assert response.status_code == 302, response.text
    login(page, app_server, user)
    page.wait_for_selector("#connect-prompt")

    start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    track = tmp_path / "riverfront.gpx"
    track.write_text(gpx_track(start, "Riverfront loop"))
    page.goto(f"{app_server}/warehouse")
    page.wait_for_load_state("networkidle")
    page.set_input_files("#import-input", str(track))
    page.wait_for_selector("#toast.show")
    assert "1 imported" in page.text_content("#toast")
    page.wait_for_selector("#activities-body tr:has-text('Riverfront loop')")

    page.set_input_files("#import-input", str(track))
    page.wait_for_function("document.querySelector('#toast').textContent.includes('Already imported')")

    page.goto(f"{app_server}/dashboard")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#recent tr:has-text('Riverfront loop')")


def test_coros_switched_off_leaves_out_the_cards_and_the_routes(app_without_coros, registered_user, page):
    login(page, app_without_coros)
    user_id = page.evaluate("document.body.dataset.userId")
    page.goto(f"{app_without_coros}/profile")
    page.wait_for_load_state("networkidle")
    assert page.locator("#coros-card").count() == 0
    page.goto(f"{app_without_coros}/dashboard")
    page.wait_for_load_state("networkidle")
    assert page.locator("#recovery-card").count() == 0
    for path in (f"/api/coros/{user_id}", f"/api/recovery/{user_id}", f"/api/readiness/{user_id}", f"/api/energy/{user_id}"):
        assert page.request.get(f"{app_without_coros}{path}").status == 404
