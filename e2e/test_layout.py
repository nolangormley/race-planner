"""Phone layout: every logged-in page fits the screen width, with touch
emulation on so the coarse-pointer sizes apply. pytest collects files by
name, so this runs after test_flows.py has synced the warehouse, linked
COROS, added races and built a plan: the pages have real content to lay out."""
import pytest

from e2e.conftest import Problems, login

# Each logged-in page and an element that shows its data has rendered.
PAGES = [
    ("/dashboard", "#predictions tbody tr"),
    ("/training-calendar", "#cal-grid .cal-cell"),
    ("/warehouse", "#activities-body tr.link"),
    ("/workout_streams/{activity_id}", "#splits tbody tr"),
    ("/profile", "#races .list-row"),
    ("/races/{race_id}/pacing", "#splits-table tbody tr"),
]

# The pages a visitor sees without an account.
PUBLIC_PAGES = [
    ("/", "#home-search"),
    ("/courses", "#course-list .course-row"),
    ("/courses/three-rivers-half/half-marathon", "#splits-table tbody tr"),
    ("/privacy", ".prose h2"),
]

# Elements that stick out past the right edge with no scrolling parent to hold them.
OVERFLOWING = """
() => [...document.querySelectorAll('body *')].filter((el) => {
  if (el.getBoundingClientRect().right <= document.documentElement.clientWidth + 0.5) return false;
  for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
    if (getComputedStyle(parent).overflowX !== 'visible') return false;
  }
  return true;
}).slice(0, 6).map((el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : '') + [...el.classList].map((name) => `.${name}`).join(''))
"""

# Chart axis labels cut off at the chart's left edge, or x labels that run
# into each other.
CRAMPED_AXES = """
() => [...document.querySelectorAll('.chart svg')].flatMap((svg) => {
  const box = svg.getBoundingClientRect();
  const chart = svg.closest('[id]')?.id || 'chart';
  const cut = [...svg.querySelectorAll('text.axis-text[text-anchor=end]')]
    .filter((node) => node.getBoundingClientRect().left < box.left - 0.5)
    .map((node) => `${chart}: "${node.textContent}" cut off`);
  const xLabels = [...svg.querySelectorAll('text.axis-text[text-anchor=middle]')];
  const touching = xLabels
    .filter((node, index) => index > 0 && node.getBoundingClientRect().left < xLabels[index - 1].getBoundingClientRect().right)
    .map((node) => `${chart}: "${node.textContent}" runs into the label before it`);
  return [...cut, ...touching];
})
"""


@pytest.fixture(params=[390, 360])
def phone(browser, request):
    context = browser.new_context(viewport={"width": request.param, "height": 800}, is_mobile=True, has_touch=True)
    page = context.new_page()
    problems = Problems()
    problems.attach(page)
    yield page
    context.close()
    assert not problems, f"browser errors: {problems}"


def test_logged_in_pages_fit_a_phone_screen(app_server, registered_user, phone):
    login(phone, app_server)
    width = phone.viewport_size["width"]
    user_id = phone.evaluate("document.body.dataset.userId")
    ids = {
        "activity_id": phone.request.get(f"{app_server}/api/activities").json()[0]["activity_id"],
        "race_id": phone.request.get(f"{app_server}/api/races/{user_id}").json()[0]["id"],
    }
    for path, marker in PAGES:
        url = path.format(**ids)
        phone.goto(f"{app_server}{url}")
        phone.wait_for_load_state("networkidle")
        phone.wait_for_selector(marker)
        overflow = phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow == 0, f"{url} is {overflow} px wider than {width} px: {phone.evaluate(OVERFLOWING)}"
        cramped = phone.evaluate(CRAMPED_AXES)
        assert not cramped, f"{url}: {cramped}"
        # The three nav links and the Sync button stay on screen without scrolling the nav.
        assert phone.evaluate("(nav => nav.scrollWidth - nav.clientWidth)(document.querySelector('.nav'))") == 0, url
        sync = phone.locator("#sync-btn").bounding_box()
        assert sync["x"] + sync["width"] <= width, url
        # The profile stays one tap away, and Log out lives on it.
        profile = phone.locator(".topbar .user-link").bounding_box()
        assert profile and profile["x"] + profile["width"] <= width, url
        if path == "/profile":
            assert phone.locator("section.card a[href='/logout']").is_visible()


def test_public_pages_fit_a_phone_screen(app_server, phone):
    width = phone.viewport_size["width"]
    for path, marker in PUBLIC_PAGES:
        phone.goto(f"{app_server}{path}")
        phone.wait_for_load_state("networkidle")
        phone.wait_for_selector(marker)
        overflow = phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow == 0, f"{path} is {overflow} px wider than {width} px: {phone.evaluate(OVERFLOWING)}"
        cramped = phone.evaluate(CRAMPED_AXES)
        assert not cramped, f"{path}: {cramped}"
        # The Courses link, Log in and Sign up stay on screen.
        assert phone.evaluate("(nav => nav.scrollWidth - nav.clientWidth)(document.querySelector('.nav'))") == 0, path
        sign_up = phone.locator(".topbar a[href='/register']").bounding_box()
        assert sign_up["x"] + sign_up["width"] <= width, path
