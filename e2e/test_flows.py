"""The flows a user actually goes through, in a real browser against the
real app, fake Strava and a fresh database. Tests run in file order: the
sync test fills the warehouse that the page tests then look at."""
import time

import requests

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
    page.wait_for_selector("#recent tbody tr")
    assert "Synced" in page.text_content("#sync-status")
    assert page.locator("#pmc-chart svg path").count() > 0
    assert page.locator("#hr-chart svg .cat-label").count() == 5     # five zone rows, bars only where time was spent
    assert page.locator("#hr-chart svg .bar").count() >= 2
    assert page.locator("#recent tbody tr").count() == 3
    assert page.locator("#predictions tbody tr").count() >= 4
    assert page.locator("#volume-chart svg .bar").count() >= 3


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
    cell = page.locator(".cal-cell:not(.other)").nth(20)
    date_key = cell.get_attribute("data-date")
    cell.click()
    page.wait_for_selector("#entry-modal.open")
    page.select_option("#entry-type", "Tempo")
    page.fill("#entry-distance", "7 miles")
    page.click("#entry-save")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    chip = page.locator(f'.cal-cell[data-date="{date_key}"] .chip')
    chip.wait_for()
    assert "Tempo" in chip.text_content()

    chip.click()
    page.wait_for_selector("#entry-modal.open")
    page.check("#entry-completed")
    page.click("#entry-save")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    page.wait_for_selector(f'.cal-cell[data-date="{date_key}"] .chip.done')

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f'.cal-cell[data-date="{date_key}"] .chip').click()
    page.wait_for_selector("#entry-modal.open")
    page.click("#entry-delete")
    page.locator("#entry-modal.open").wait_for(state="hidden")
    selector = f'.cal-cell[data-date="{date_key}"] .chip'
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
