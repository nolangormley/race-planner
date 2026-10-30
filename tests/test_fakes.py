"""The fake Strava and COROS in e2e/ that the browser tests and the demo use."""
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from e2e import fake_coros, fake_strava
from src.coros import normalize_day


def started(run):
    return datetime.strptime(run["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def test_default_runs_stay_the_three_browser_test_runs():
    runs = fake_strava.synthetic_runs()
    assert [run["distance"] for run in runs] == [8000.0, 8200.0, 8400.0]
    assert {run["max_heartrate"] for run in runs} == {185.0}
    assert all("workout_type" not in run for run in runs)


def test_history_spans_the_window_newest_first_with_long_runs_and_workouts():
    now = datetime.now(timezone.utc)
    runs = fake_strava.synthetic_runs(60, span_days=175)
    starts = [started(run) for run in runs]
    assert len({run["id"] for run in runs}) == 60
    assert starts == sorted(starts, reverse=True)
    assert all(1 <= (now.date() - start.date()).days <= 175 for start in starts)
    assert (now - starts[-1]).days >= 170                                    # reaches back about six months

    by_kind = {kind: [run for run in runs if run["workout_type"] == kind] for kind in (fake_strava.EASY, fake_strava.RACE, fake_strava.LONG_RUN, fake_strava.WORKOUT)}
    assert len(by_kind[fake_strava.LONG_RUN]) >= 10 and len(by_kind[fake_strava.WORKOUT]) >= 10
    assert {run["name"] for run in by_kind[fake_strava.RACE]} == {"5K race", "10K race"}
    assert min(run["average_speed"] for run in by_kind[fake_strava.RACE]) > max(run["average_speed"] for run in by_kind[fake_strava.EASY])
    assert min(run["distance"] for run in by_kind[fake_strava.LONG_RUN]) > max(run["distance"] for run in by_kind[fake_strava.EASY])
    assert {run["name"] for run in by_kind[fake_strava.WORKOUT]} == {"Tempo run", "6 x 3 min"}
    easy_speeds = [run["average_speed"] for run in by_kind[fake_strava.EASY]]
    assert easy_speeds[0] > easy_speeds[-1]                                   # fitter at the end of the block


def test_history_streams_agree_with_the_summary_and_laps_follow_the_miles():
    run = fake_strava.synthetic_runs(60, span_days=175)[1]
    streams = fake_strava.shaped_streams(run)
    velocity, heartrate = streams["velocity_smooth"]["data"], streams["heartrate"]["data"]
    assert len(velocity) == len(heartrate) == len(streams["time"]["data"]) == run["moving_time"]
    assert abs(sum(velocity) - run["distance"]) < 1
    assert abs(sum(heartrate) / len(heartrate) - run["average_heartrate"]) < 1
    assert max(heartrate) == run["max_heartrate"]

    laps = fake_strava.mile_laps(streams)
    starts = [lap["start_index"] for lap in laps]
    assert starts[0] == 0 and starts == sorted(set(starts))                  # monotone, no overlap
    assert [lap["end_index"] + 1 for lap in laps[:-1]] == starts[1:]
    assert all(abs(lap["distance"] - fake_strava.METERS_PER_MILE) < 6 for lap in laps[:-1])
    assert abs(sum(lap["distance"] for lap in laps) - run["distance"]) < 1


def test_activity_list_pages_and_honours_after():
    runs = fake_strava.synthetic_runs(60, span_days=175)
    client = TestClient(fake_strava.make_app(runs))
    first = client.get("/api/v3/athlete/activities", params={"page": 1, "per_page": 50}).json()
    second = client.get("/api/v3/athlete/activities", params={"page": 2, "per_page": 50}).json()
    assert len(first) == 50 and len(second) == 10
    assert [run["id"] for run in first + second] == [run["id"] for run in runs]

    month_ago = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    recent = client.get("/api/v3/athlete/activities", params={"after": month_ago}).json()
    assert recent and all(started(run).timestamp() > month_ago for run in recent)
    assert len(recent) < 15
    assert len(client.get(f"/api/v3/activities/{runs[0]['id']}/laps").json()) >= 3


def test_nights_are_one_per_day_without_sleep_and_parse():
    today = date(2026, 11, 4)
    nights = fake_coros.synthetic_nights(today, 60)
    days = [normalize_day(night)["day"] for night in nights]
    assert days == [today - timedelta(days=offset) for offset in range(60)]
    assert all("tib" not in night for night in nights)
    assert all(40 <= night["rhr"] <= 55 and 45 <= night["avgSleepHrv"] <= 75 for night in nights)
    assert len(fake_coros.synthetic_nights(today)) == fake_coros.NIGHTS == 14
