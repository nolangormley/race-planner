"""The flows a user actually goes through, in a real browser against the
real app, fake Strava and a fresh database. Tests run in file order: the
sync test fills the warehouse that the page tests then look at."""
import time
from datetime import date, timedelta

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

