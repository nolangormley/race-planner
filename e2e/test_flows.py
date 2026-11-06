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

    page.fill("#race_name", "three rivers")                  # the fake race-courses catalog
    page.click("#race_name-races [role=option]:has-text('Three Rivers Half')")
    expect(page.locator("#race_length")).to_have_value("half marathon")
    expect(page.locator("#race-course-note")).to_contain_text("course map comes with the race")
    page.fill("#race_date", "2027-04-10")
    page.click("button[type=submit]")
    page.wait_for_url("**/strava-auth")
    assert "Authorize with Strava" in page.content()

    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    races = page.text_content("#races")
    assert "Three Rivers Half" in races and "half marathon" in races and "course map" in races
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


def test_planned_distances_follow_the_unit_switch(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    today = date.today().isoformat()
    created = page.request.post(f"{app_server}/api/training-plan/{user_id}/entry", data={
        "date": today, "workout_type": "Easy Run", "approximate_distance": "5 miles",
    })
    assert created.ok, created.text()
    page.reload()
    page.click('#units-toggle button[data-units="metric"]')
    expect(page.locator("#week-plan tr.today")).to_contain_text("Easy Run 8 km")

    page.goto(f"{app_server}/training-calendar")
    chip = page.locator(f'.cal-cell[data-date="{today}"] .chip:not(.activity) .dist')
    expect(chip).to_have_text("8 km")
    expect(page.locator("#upcoming .dist").first).to_have_text("8 km")
    distances = page.locator(".chip:not(.activity) .dist").all_text_contents()
    assert all(text.endswith(" km") for text in distances), distances    # the plan was built in miles

    def planned_km():                                                    # "of 144 km planned", or none planned
        found = re.search(r"of (\d+) km planned", page.text_content("#month-summary"))
        return int(found.group(1)) if found else 0

    planned_before = planned_km()

    page.click("#cal-add")
    expect(page.locator("#entry-distance")).to_have_value("8 km")
    expect(page.locator("#entry-distance")).to_have_attribute("placeholder", "10 km")
    page.fill("#entry-distance", "45 min")
    page.click("#entry-save")
    expect(chip).to_have_text("45 min")                                  # not a distance, so as typed
    # The 8 km left the month's total (shown to the whole km) and 45 min added nothing.
    assert planned_before - 9 <= planned_km() <= planned_before - 8
    assert page.request.delete(f"{app_server}/api/training-plan/entry/{created.json()['id']}").ok


def test_planned_race_sits_inside_the_fitness_chart(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    label = page.locator("#pmc-chart .marker-label", has_text="Spring Half")
    label.wait_for()
    chart, box = page.locator("#pmc-chart svg").bounding_box(), label.bounding_box()
    assert chart["x"] <= box["x"] and box["x"] + box["width"] <= chart["x"] + chart["width"], (chart, box)
    assert box["y"] >= chart["y"], (chart, box)

    # The solid lines stop today, so the right edge carries the planned values on race day:
    # those of its morning, before the race's own load.
    days = page.request.get(f"{app_server}/api/projection/{user_id}").json()["days"]
    race_day = days[-1]
    assert race_day["workout_type"] == "Race" and race_day["load"] > 0, race_day
    assert (race_day["ctl"], race_day["atl"]) == (days[-2]["ctl"], days[-2]["atl"]), days[-2:]
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

    # Day by day, each row adds up to the next one's level, and the last to the level now.
    page.click("#energy-ledger summary")
    rows = page.locator("#energy-ledger tbody tr")
    expect(rows.last).to_have_text(re.compile(r"^Now"))
    points = lambda text: 0 if text.strip() == "–" else abs(int(text.split("\n")[0]))
    cells = [row.locator("td").all_inner_texts() for row in rows.all()]
    for row, following in zip(cells, cells[1:]):
        assert points(row[1]) - points(row[2]) - points(row[3]) + points(row[4]) == points(following[1]), cells
    assert cells[-1][1] == page.text_content("#energy-value").split("/")[0], cells


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
    # Pace and split time of every row but the short last one.
    paces_and_splits = "rows => rows.slice(0, -1).map((row) => [row.cells[2].textContent, row.cells[3].textContent])"
    assert all(pace == split for pace, split in page.eval_on_selector_all("#splits-table tbody tr", paces_and_splits))
    even_first_row = rows.nth(0).text_content()

    page.click('#strategy button[data-strategy="negative"]')
    page.wait_for_function(
        "text => document.querySelector('#splits-table tbody tr').textContent !== text", arg=even_first_row,
    )
    assert "1:45:00" in rows.nth(13).text_content()             # the finish time does not move
    assert page.locator("#pace-band tr").count() == 14
    assert all(pace == split for pace, split in page.eval_on_selector_all("#splits-table tbody tr", paces_and_splits))

    page.fill("#goal", "1:45")                                  # how most runners write 1 h 45 min
    page.click("#pacing-form button[type=submit]")
    expect(page.locator("#goal-source")).to_contain_text("Goal as typed")
    expect(page.locator("#pacing-summary")).to_contain_text("8:01/mi")
    assert "1:45:00" in rows.nth(13).text_content()

    page.fill("#goal", "0:30:00")
    page.click("#pacing-form button[type=submit]")
    expect(page.locator("#pacing-status")).to_contain_text("Goal 0:30:00 is not a plausible half marathon time")
    expect(rows).to_have_count(0)
    expect(page.locator("#pace-band tr")).to_have_count(0)
    expect(page.locator("#goal-source")).to_have_text("")
    # The browser logs the refusal, a 400, as an error. This test expects it.
    refusal = f"/api/races/{race_id}/pacing"
    page.problems[:] = [problem for problem in page.problems if not ("status of 400" in problem and refusal in problem)]


def test_pacing_page_takes_a_course_from_the_course_library(app_server, registered_user, page):
    login(page, app_server)
    user_id = page.evaluate("document.body.dataset.userId")
    response = page.request.post(f"{app_server}/api/races/{user_id}", data={
        "name": "Library Half", "race_length": "half marathon",
        "race_date": (date.today() + timedelta(weeks=10)).isoformat(), "goal_time": "1:50:00",
    })
    assert response.ok, response.text()
    race_id = response.json()["id"]

    page.goto(f"{app_server}/races/{race_id}/pacing")
    page.wait_for_selector("#splits-table tbody tr")
    expect(page.locator("#profile-chart")).to_contain_text("No course yet")

    page.click("#course-find")
    rows = page.locator("#library-list .list-row")
    expect(rows).to_have_count(2)                               # the halves with a course file, not the marathon
    expect(rows.nth(0)).to_contain_text("Allegheny Half")
    expect(rows.nth(0).locator(".badge-warning")).to_have_text("Check it")
    page.fill("#library-search", "pittsburgh")                  # the city
    expect(rows).to_have_count(1)
    expect(rows.nth(0)).to_contain_text("Three Rivers Half")
    expect(rows.nth(0)).to_contain_text("13.1 mi")
    page.fill("#library-search", "boston")
    expect(page.locator("#library-list")).to_contain_text("No match")
    page.fill("#library-search", "three rivers")
    rows.nth(0).get_by_role("button", name="Use").click()

    expect(page.locator("#library-modal")).to_be_hidden()
    expect(page.locator("#pacing-summary")).to_contain_text("13.1 mi")
    expect(page.locator("#gpx-remove")).to_be_visible()
    expect(page.locator("#profile-chart")).not_to_contain_text("No course yet")
    expect(page.locator("#splits-table.no-grade")).to_have_count(0)
    assert "1:50:00" in page.locator("#splits-table tbody tr").last.text_content()


def test_profile_race_search_names_the_race_and_brings_its_course(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    race_day = (date.today() + timedelta(weeks=40)).isoformat()

    page.click("#add-race")
    page.fill("#r-name", "monon")
    expect(page.locator("#r-name-races [role=option]")).to_have_count(1)
    page.press("#r-name", "ArrowDown")
    page.press("#r-name", "Enter")                              # picks the race, does not submit the form
    expect(page.locator("#r-name")).to_have_value("Monongahela Half – Half Marathon")
    expect(page.locator("#r-length")).to_have_value("half marathon")
    expect(page.locator("#r-course-note")).to_contain_text("No course map")
    page.fill("#r-date", race_day)
    page.click("#race-form button[type=submit]")
    expect(page.locator("#races")).to_contain_text("Monongahela Half")

    page.click("#add-race")
    page.fill("#r-name", "three rivers")
    expect(page.locator("#r-name-races [role=option]")).to_have_count(2)   # the half and the marathon
    page.click("#r-name-races [role=option]:has-text('Three Rivers Marathon')")
    expect(page.locator("#r-length")).to_have_value("marathon")
    page.fill("#r-date", race_day)
    page.click("#race-form button[type=submit]")
    expect(page.locator("#races .list-row", has_text="Three Rivers Marathon")).to_contain_text("course map")
    expect(page.locator("#races .list-row", has_text="Monongahela Half")).not_to_contain_text("course map")

    # Two events of one race whose name says both: the meta line tells them apart, and the name stored is
    # the race's, not the event's.
    page.click("#add-race")
    page.fill("#r-name", "steel city")
    options = page.locator("#r-name-races [role=option]")
    expect(options).to_have_count(2)
    expect(options.nth(0).locator(".race-search-meta")).to_have_text("10 Miler · Pittsburgh, PA · May · course map")
    expect(options.nth(1).locator(".race-search-meta")).to_have_text("10K · Pittsburgh, PA · May · course map")
    options.nth(1).click()
    expect(page.locator("#r-name")).to_have_value("Steel City 10 Miler & 10K")
    expect(page.locator("#r-length")).to_have_value("10k")
    page.click("#cancel-race")

def test_a_visitor_finds_a_course_and_its_pace_band_without_an_account(app_server, page):
    page.goto(f"{app_server}/")
    expect(page.locator("h1")).to_have_text("Pace your race on its real course.")
    page.fill("#home-search", "three rivers")                  # the fake race-courses catalog
    page.click("#home-search-races [role=option]:has-text('Three Rivers Half')")
    page.wait_for_url("**/courses/three-rivers-half/half-marathon")
    expect(page.locator("#goal")).to_have_value("1:58:00")      # 13.1 miles at the example 9:00 a mile
    expect(page.locator("#goal-source")).to_contain_text("Example goal")
    expect(page.locator("#pacing-summary")).to_contain_text("ft up")
    expect(page.locator("#profile-chart svg").first).to_be_visible()

    page.fill("#goal", "1:45:00")
    page.click("#pacing-form button[type=submit]")
    expect(page.locator("#splits-table tbody tr").last).to_contain_text("1:45:00")
    expect(page.locator("#goal-source")).to_have_text("")
    expect(page.locator("#pace-band tr")).to_have_count(14)
    expect(page.locator(".course-cta a")).to_have_attribute("href", "/register")

    page.goto(f"{app_server}/courses")
    rows = page.locator(".course-row:not(.hidden)")
    expect(rows).to_have_count(5)                               # the five events with a course file
    page.fill("#course-filter", "allegheny")
    expect(rows).to_have_count(1)
    page.fill("#course-filter", "steel city")
    expect(rows).to_have_count(2)                               # one race, two events: each row says which
    expect(rows.nth(0)).to_contain_text("10 Miler · Pittsburgh, PA")
    expect(rows.nth(1)).to_contain_text("10K · Pittsburgh, PA")
    page.fill("#course-filter", "boston")
    expect(page.locator("#course-empty")).to_be_visible()

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
    # tempo and too few reps for intervals, so the card says nothing was found.
    assert structure == {"kind": None, "checked": True, "pending": False, "reps": [], "recoveries": []}
    page.goto(f"{app_server}/workout_streams/{run['activity_id']}")
    page.wait_for_selector("#streams-chart svg path")
    expect(page.locator("#structure-card")).to_be_visible()
    expect(page.locator("#structure-sub")).to_have_text("No intervals or tempo found.")
    expect(page.locator("#structure-reps")).to_have_count(0)
    assert page.locator("#streams-chart .x-band").count() == 0


def test_activity_page_says_when_the_structure_pass_has_not_looked_yet(app_server, registered_user, page):
    login(page, app_server)
    run = page.request.get(f"{app_server}/api/activities").json()[0]
    intervals = f"**/api/activities/{run['activity_id']}/intervals"
    unchecked = {"kind": None, "checked": False, "pending": True, "reps": [], "recoveries": []}
    # A run synced before the structure pass existed.
    page.route(intervals, lambda route: route.fulfill(json=unchecked))
    page.goto(f"{app_server}/workout_streams/{run['activity_id']}")
    expect(page.locator("#structure-sub")).to_have_text("Not analysed yet. The next sync looks for intervals and tempo.")
    expect(page.locator("#structure-card")).to_be_visible()

    # An activity the pass never looks at, such as a ride, gets no card.
    page.unroute(intervals)
    page.route(intervals, lambda route: route.fulfill(json={**unchecked, "pending": False}))
    page.goto(f"{app_server}/workout_streams/{run['activity_id']}")
    page.wait_for_selector("#streams-chart svg path")
    page.wait_for_load_state("networkidle")
    assert "hidden" in page.get_attribute("#structure-card", "class")


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


def test_activities_page_searches_names_and_shows_fifty_rows_at_a_time(app_server, registered_user, page):
    login(page, app_server)
    synced = len(page.request.get(f"{app_server}/api/activities?limit=1000").json())
    # A long history the fakes do not give: every third activity is a hill
    # session and every fourth a ride, and each is 10 m longer than the last.
    start = datetime(2025, 1, 6, 7, 0)
    history = [{
        "activity_id": 900 + index, "name": f"Hill repeats {index}" if index % 3 == 0 else f"Steady {index}",
        "type": "Ride" if index % 4 == 0 else "Run", "start_date_local": (start + timedelta(days=index)).isoformat(),
        "distance": 5000 + 10 * index, "moving_time": 1800, "average_speed": 2.8, "average_heartrate": 140, "training_load": 40,
    } for index in range(120)]

    def with_history(route):
        response = route.fetch()
        route.fulfill(response=response, json=response.json() + history)

    page.route("**/api/activities?limit=1000", with_history)
    page.goto(f"{app_server}/warehouse")
    rows = page.locator("#activities-body tr.link")
    count = page.locator("#activities-sub")
    total = synced + len(history)
    expect(rows).to_have_count(50)
    expect(count).to_contain_text(f"{total} activities")                 # every activity, not only the rows shown
    expect(page.locator("#activities-shown")).to_have_text(f"Showing 50 of {total}")
    page.click("#show-more")
    expect(rows).to_have_count(100)
    page.click("#show-more")
    expect(rows).to_have_count(total)
    expect(page.locator("#activities-more")).to_be_hidden()

    page.fill("#activity-search", "HILL")                                # case does not matter
    expect(count).to_contain_text("40 activities")
    expect(rows).to_have_count(40)
    page.select_option("#type-filter", "Ride")
    expect(count).to_contain_text("10 activities")
    page.select_option("#type-filter", "")
    page.click("#activities-table th[data-sort=distance]")
    expect(rows.first).to_contain_text("Hill repeats 117")                # the longest first
    page.fill("#activity-search", "no such name")
    expect(count).to_have_text("No activities match.")
    expect(page.locator("#activities-body")).to_contain_text("No matching activities")
    page.fill("#activity-search", "")
    expect(rows).to_have_count(50)                                        # a new search starts from 50 rows again

    page.route("**/api/activities/1017", lambda route: route.fulfill(json={"message": "Activity deleted successfully"}))
    page.once("dialog", lambda dialog: dialog.accept())
    page.fill("#activity-search", "hill repeats 117")
    page.click("#activities-body [data-delete='1017']")
    expect(count).to_have_text("No activities match.")
    page.fill("#activity-search", "")
    expect(count).to_contain_text(f"{total - 1} activities")

    page.click("#tabs button[data-tab=workouts]")
    expect(page.locator("#tab-workouts")).to_be_visible()
    expect(page.locator("#tab-activities")).to_be_hidden()
    page.click("#tabs button[data-tab=activities]")
    expect(rows).to_have_count(50)


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
    # With a run in the warehouse the card asking to connect Strava or import files is gone, and the
    # acute:chronic ratio waits for four weeks of history instead of calling one run an injury risk.
    expect(page.locator("#connect-prompt")).to_be_hidden()
    fatigue = page.locator("#stat-fatigue .stat-foot")
    expect(fatigue).to_contain_text("needs four weeks of runs")
    assert not re.search(r"injury|detraining|sweet spot|\d\.\d\d", fatigue.text_content())


def test_an_imported_run_marks_the_planned_run_done(app_server, page, tmp_path):
    user = {"email": f"planned{int(time.time())}@example.com", "password": "file-runner-123", "name": "Planned Runner"}
    response = requests.post(f"{app_server}/register", data={
        **user, "date_of_birth": "1991-03-04", "gender": "Female", "height": "1.65", "weight": "58",
    }, allow_redirects=False)
    assert response.status_code == 302, response.text
    login(page, app_server, user)
    user_id = page.evaluate("document.body.dataset.userId")
    start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    created = page.request.post(f"{app_server}/api/training-plan/{user_id}/entry", data={
        "date": start.date().isoformat(), "workout_type": "Easy Run", "approximate_distance": "3 miles",
    })
    assert created.ok, created.text()

    track = tmp_path / "planned.gpx"
    track.write_text(gpx_track(start, "Planned easy"))
    page.goto(f"{app_server}/warehouse")
    page.wait_for_load_state("networkidle")
    page.set_input_files("#import-input", str(track))
    page.wait_for_function("document.querySelector('#toast').textContent.includes('Marked 1 planned session done')")

    entries = page.request.get(f"{app_server}/api/training-plan/{user_id}").json()["entries"]
    entry = next(item for item in entries if item["id"] == created.json()["id"])
    assert entry["is_completed"]
    assert entry["strava_activity_id"]


def test_feedback_button_sends_a_note_from_any_page(app_server, registered_user, page):
    login(page, app_server)
    page.goto(f"{app_server}/training-calendar")
    page.wait_for_load_state("networkidle")
    page.click("#feedback-btn")
    page.wait_for_selector("#feedback-modal.open")
    page.fill("#feedback-message", "The calendar could show the week's total.")
    page.check("#feedback-contact")
    page.click("#feedback-form button[type=submit]")
    page.locator("#feedback-modal.open").wait_for(state="hidden")
    page.wait_for_selector("#toast.show")
    assert "Thanks" in page.text_content("#toast")


def test_admin_page_shows_sign_ups_and_notes_to_an_admin_only(app_server, registered_user, page):
    login(page, app_server)                                     # ADMIN_EMAILS names the e2e user
    page.click(".nav a[href='/admin']")
    page.wait_for_url("**/admin")
    rows = page.locator("#cohorts tbody tr")
    expect(rows).to_have_count(9)                               # eight weeks and all time
    this_week = [int(cell) for cell in rows.nth(7).locator("td.num").all_text_contents()]
    all_time = [int(cell) for cell in rows.nth(8).locator("td.num").all_text_contents()]
    assert this_week[0] >= 1 and all_time[0] >= this_week[0]
    assert all(count >= 1 for count in this_week[1:4])          # connected data, a race, a course file
    assert this_week[4] == 0 == all_time[4]                     # nobody signed up a week ago
    note = page.locator("#feedback-table tbody tr").first
    expect(note).to_contain_text("The calendar could show the week's total.")
    expect(note).to_contain_text(registered_user["email"])
    expect(note).to_contain_text("/training-calendar")

    other = {"email": f"other{int(time.time())}@example.com", "password": "other-password-1", "name": "Other Runner"}
    response = requests.post(f"{app_server}/register", data={
        **other, "date_of_birth": "1991-03-04", "gender": "Male", "height": "1.8", "weight": "75",
    }, allow_redirects=False)
    assert response.status_code == 302, response.text
    page.goto(f"{app_server}/logout")
    login(page, app_server, other)
    # The dashboard's reads must finish before the logout below, or one answers 401 after it.
    page.wait_for_load_state("networkidle")
    expect(page.locator(".nav a[href='/admin']")).to_have_count(0)
    assert page.request.get(f"{app_server}/admin").status == 404
    assert page.request.get(f"{app_server}/api/admin/stats").status == 404
    page.goto(f"{app_server}/logout")
    assert page.request.get(f"{app_server}/admin").status == 404


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


def test_the_privacy_page_renders_signed_out(app_server, page):
    page.goto(f"{app_server}/privacy")
    expect(page.locator("h1")).to_have_text("Privacy")
    text = page.text_content("article")
    assert "Nolan Gormley" in text and "Last updated: November 6, 2026" in text
    expect(page.locator(".topbar a[href='/login']")).to_be_visible()      # signed out: the visitor top bar
    expect(page.locator("footer a[href='/privacy']")).to_be_visible()
    page.goto(f"{app_server}/login")
    expect(page.locator(".auth a[href='/privacy']")).to_be_visible()


def test_a_runner_deletes_their_account_from_the_profile(app_server, page, tmp_path):
    user = {"email": f"leaving{int(time.time())}@example.com", "password": "leaving-123456", "name": "Leaving Runner"}
    page.goto(f"{app_server}/register")
    page.fill("#name", user["name"])
    page.fill("#email", user["email"])
    page.fill("#password", user["password"])
    page.fill("#date_of_birth", "1993-06-07")
    page.select_option("#gender", "Other")
    page.fill("#height-ft", "5")
    page.fill("#height-in", "9")
    page.fill("#weight-input", "150")
    page.click("button[type=submit]")
    page.wait_for_url("**/setup-race")
    # Rows in the warehouse to delete as well: an imported run.
    start = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=7, minute=0, second=0, microsecond=0)
    track = tmp_path / "last.gpx"
    track.write_text(gpx_track(start, "Last run"))
    page.goto(f"{app_server}/warehouse")
    page.wait_for_load_state("networkidle")
    page.set_input_files("#import-input", str(track))
    page.wait_for_selector("#activities-body tr:has-text('Last run')")

    page.goto(f"{app_server}/profile")
    page.wait_for_load_state("networkidle")
    page.click("#delete-account")
    page.wait_for_selector("#delete-modal.open")
    page.fill("#delete-password", "not-the-password")
    page.click("#delete-confirm")
    expect(page.locator("#delete-form .status")).to_have_text("The password is incorrect.")
    page.fill("#delete-password", user["password"])
    page.click("#delete-confirm")
    page.wait_for_url(re.compile(r"/login\?deleted=1$"))
    expect(page.locator(".alert-info")).to_have_text("Your account and data were deleted.")

    page.fill("input[name=username]", user["email"])
    page.fill("input[name=password]", user["password"])
    page.click("button[type=submit]")
    expect(page.locator(".alert-error")).to_have_text("Invalid email or password.")
    # The browser logs the refused password, a 400, as an error. This test expects it.
    page.problems[:] = [problem for problem in page.problems if not ("status of 400" in problem and "/api/account" in problem)]
