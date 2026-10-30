"""Energy: a 0 to 100 estimate of how charged the body is, hour by hour,
in the spirit of Garmin's Body Battery. Pure: no I/O.

The level recharges at night by how well the night went, drains while
awake and with every activity, carries over from one day to the next and
stays between 0 and 100. Garmin reads stress from heart rate variability
all day (Firstbeat's method, below). COROS's web API gives one overnight
HRV average and one resting heart rate a night, so this model uses those,
the training load (TRIMP) of each activity and a fixed daily schedule.

Recharge. The quality q of a night is the share of the gap to 100 that
a night of NOMINAL_SLEEP_HOURS closes:

    q = clip(Q_BASE + W_HRV * d_hrv - W_RHR * d_rhr, Q_MIN, Q_MAX)

For ln(HRV) and for resting heart rate in bpm, against the mean and
standard deviation of the BASELINE_NIGHTS nights before the night:

    z = (x - mean) / max(sd, floor)
    d = sign(z) * max(0, |z| - SWC_Z)     only the part outside the smallest worthwhile change

d is clipped to HRV_DEV_LIMITS or RHR_DEV_LIMITS. A night is never part of
its own baseline. A measure with fewer than MIN_BASELINE_NIGHTS earlier
readings, or no reading, counts as normal (d = 0). Each hour asleep closes
1 - (1 - q) ** (1 / NOMINAL_SLEEP_HOURS) of the gap that is left, so a
longer measured night charges more and a shorter one less.

Drain. WAKING_DRAIN_PER_HOUR for each hour awake, and DRAIN_PER_TRIMP for
each point of an activity's Banister TRIMP, spread evenly over its elapsed
time from its start.

Schedule. The night ends at WAKE_HOUR, or at the start of the first
activity of the day when that is earlier (not before EARLIEST_WAKE_HOUR).
It lasts NOMINAL_SLEEP_HOURS, or the measured sleep when a source gives it.
So without sleep data and early activities, waking hours are 07:00 to 23:00.

Trend. This morning's level minus the mean of the TREND_MORNINGS mornings
before it.

Calibration (tests/test_energy.py): a normal or good night after a day with
an easy run refills to 80 to 95; a 60-TRIMP easy run costs about 13 and a
150-TRIMP hard session about 33; a night with HRV well under its baseline
and resting heart rate up refills at least 15 points less.

Sources:
- Firstbeat Technologies (2014). Stress and Recovery Analysis Method Based
  on 24-hour Heart Rate Variability. White paper. Body resources build up
  in recovery and are used by stress and physical activity. Garmin's Body
  Battery runs on the Firstbeat engine; its formula is not published.
- Plews DJ, Laursen PB, Kilding AE, Buchheit M (2012). Heart rate
  variability in elite triathletes, is variation in variability the key to
  effective training? Eur J Appl Physiol 112:3729-41; and Plews et al.
  (2013), Training adaptation and heart rate variability in elite endurance
  athletes. Sports Med 43(9):773-81. ln(rMSSD) against the athlete's own
  baseline, judged by the smallest worthwhile change.
- Vesterinen V et al. (2016). Individual endurance training prescription
  with heart rate variability. Med Sci Sports Exerc 48(7):1347-54. The
  smallest worthwhile change is the mean +- 0.5 SD of four weeks of readings.
- Kiviniemi AM, Hautala AJ, Kinnunen H, Tulppo MP (2007). Endurance training
  guided individually by daily heart rate variability measurements. Eur J
  Appl Physiol 101(6):743-51. HRV under the athlete's recent mean minus one
  SD means an easy day.
- Altini M, Plews DJ (2021). What is behind changes in resting heart rate
  and heart rate variability? Sensors 21(23):7932. HRV is the more
  sensitive marker of the two, so it has twice the weight of resting
  heart rate here.
- WHOOP Recovery, Oura Readiness and Garmin Training Readiness combine
  similar overnight inputs; none publishes its formula.

What it cannot see: stress during the day (the main drain of Body Battery
outside exercise), naps, when you actually slept, illness, alcohol, and
activities without heart rate. It is an estimate, not a measurement.
"""
from datetime import datetime, time, timedelta
from math import exp, log
from statistics import mean, stdev

FULL = 100.0
START_LEVEL = 50.0           # level at the start of the warm-up
WARMUP_DAYS = 3              # simulated before the first shown day so the start level washes out
DEFAULT_DAYS = 7
HOUR = timedelta(hours=1)

# Recharge
Q_BASE = 0.78                # share of the gap to 100 a normal night closes
Q_MIN = 0.25
Q_MAX = 0.90
W_HRV = 0.18                 # change of q for each SD of ln(HRV) outside the band
W_RHR = 0.09                 # change of q for each SD of resting heart rate outside the band
SWC_Z = 0.5                  # smallest worthwhile change, in standard deviations
HRV_DEV_LIMITS = (-3.0, 1.5)
RHR_DEV_LIMITS = (-1.5, 3.0)
BASELINE_NIGHTS = 28
MIN_BASELINE_NIGHTS = 7
LN_HRV_SD_FLOOR = 0.05       # about 5 % of HRV, so a very steady history does not magnify small changes
RHR_SD_FLOOR = 1.5           # bpm

# Schedule
WAKE_HOUR = 7
EARLIEST_WAKE_HOUR = 4
NOMINAL_SLEEP_HOURS = 8.0

# Drain
WAKING_DRAIN_PER_HOUR = 2.5
DRAIN_PER_TRIMP = 0.22
MIN_ACTIVITY_SECONDS = 60

TREND_MORNINGS = 7


def first_day(today, days):
    """The first day energy() simulates. The baselines also read the
    BASELINE_NIGHTS nights before it."""
    return today - timedelta(days=WARMUP_DAYS + max(days, TREND_MORNINGS))


def clip(value, low, high):
    return min(high, max(low, value))


def deviation(value, history, sd_floor):
    """(z, mean) of value against its history, or (None, None) without a
    value or with too short a history."""
    if value is None or len(history) < MIN_BASELINE_NIGHTS:
        return None, None
    center = mean(history)
    return (value - center) / max(stdev(history), sd_floor), center


def outside_band(z):
    """The part of a z-score beyond the smallest worthwhile change."""
    if z is None:
        return 0.0
    return max(0.0, abs(z) - SWC_Z) * (1 if z > 0 else -1)


def assess_night(night, history):
    """The quality of one night with the readings and baselines behind it.
    history: the nights before it within BASELINE_NIGHTS."""
    hrv, rhr = night.get("hrv"), night.get("rhr")
    hrv_z, ln_center = deviation(log(hrv) if hrv else None, [log(item["hrv"]) for item in history if item["hrv"]], LN_HRV_SD_FLOOR)
    rhr_z, rhr_center = deviation(rhr, [item["rhr"] for item in history if item["rhr"]], RHR_SD_FLOOR)
    quality = Q_BASE + W_HRV * clip(outside_band(hrv_z), *HRV_DEV_LIMITS) - W_RHR * clip(outside_band(rhr_z), *RHR_DEV_LIMITS)
    return {
        "reading": bool(hrv or rhr),
        "hrv": hrv,
        "hrv_baseline": exp(ln_center) if ln_center is not None else None,
        "hrv_z": hrv_z,
        "rhr": rhr,
        "rhr_baseline": rhr_center,
        "rhr_z": rhr_z,
        "sleep_min": night.get("sleep_min"),
        "quality": clip(quality, Q_MIN, Q_MAX),
    }


def describe_night(night):
    """What drove the charge of a night, in words."""
    if not night["reading"]:
        return "no COROS reading, so the usual rate"
    parts = []
    if night["hrv_z"] is not None:
        change = night["hrv"] / night["hrv_baseline"] - 1
        parts.append("HRV in your normal range" if abs(night["hrv_z"]) <= SWC_Z
                     else f"HRV {abs(change):.0%} {'over' if change > 0 else 'under'} baseline")
    if night["rhr_z"] is not None:
        delta = night["rhr"] - night["rhr_baseline"]
        parts.append("resting heart rate in your normal range" if abs(night["rhr_z"]) <= SWC_Z
                     else f"resting heart rate {abs(delta):.0f} bpm {'over' if delta > 0 else 'under'} baseline")
    if parts == ["HRV in your normal range", "resting heart rate in your normal range"]:
        return "HRV and resting heart rate in your normal range"
    return ", ".join(parts) or "your baseline is still building"


def overlap_hours(start, end, left, right):
    return max(0.0, (min(end, right) - max(start, left)) / HOUR)


def energy(nights, activities, now, days=DEFAULT_DAYS):
    """The energy level hour by hour over the last days up to now, with
    this morning's level, its trend and the reasons behind today.

    nights: [{"day", "hrv", "rhr", "sleep_min"}], where the row for day D is
    the night that ends on the morning of D; a missing reading is None.
    activities: [{"activity_id", "name", "type", "start", "seconds", "trimp"}],
    with start in local time like now.
    """
    today = now.date()
    start_day = first_day(today, days)
    by_day = {night["day"]: night for night in nights}
    windows = []
    for offset in range((today - start_day).days + 1):
        day = start_day + timedelta(days=offset)
        history = [night for night in nights if day - timedelta(days=BASELINE_NIGHTS) <= night["day"] < day]
        night = assess_night(by_day.get(day, {}), history)
        wake = datetime.combine(day, time(WAKE_HOUR))
        early = [activity["start"] for activity in activities if activity["start"].date() == day and activity["start"] < wake]
        if early:
            wake = max(min(early), datetime.combine(day, time(EARLIEST_WAKE_HOUR)))
        hours = night["sleep_min"] / 60 if night["sleep_min"] else NOMINAL_SLEEP_HOURS
        windows.append({**night, "day": day, "bed": wake - timedelta(hours=hours), "wake": wake, "charge": 0.0})

    # Steps are whole hours plus every wake time and now, so the morning
    # and current levels are exact. Before the wake time today, the night
    # runs on to give this morning's level.
    begin = datetime.combine(start_day, time())
    end = max(now, windows[-1]["wake"])
    marks = {begin + HOUR * step for step in range(int((end - begin) / HOUR) + 1)}
    marks.update(window["wake"] for window in windows)
    marks.add(now)
    marks = sorted(marks)
    level = START_LEVEL
    levels = {begin: level}
    for left, right in zip(marks, marks[1:]):
        asleep = 0.0
        for window in windows:
            hours = overlap_hours(window["bed"], window["wake"], left, right)
            if hours:
                gain = (1 - (1 - window["quality"]) ** (hours / NOMINAL_SLEEP_HOURS)) * (FULL - level)
                window["charge"] += gain
                level += gain
                asleep += hours
        level -= WAKING_DRAIN_PER_HOUR * max(0.0, (right - left) / HOUR - asleep)
        for activity in activities:
            seconds = max(activity["seconds"] or 0, MIN_ACTIVITY_SECONDS)
            hours = overlap_hours(activity["start"], activity["start"] + timedelta(seconds=seconds), left, right)
            level -= DRAIN_PER_TRIMP * activity["trimp"] * hours * 3600 / seconds
        level = clip(level, 0.0, FULL)
        levels[right] = level

    mornings = {window["day"]: levels[window["wake"]] for window in windows}
    morning = round(mornings[today])
    usual = round(mean(mornings[today - timedelta(days=offset)] for offset in range(1, TREND_MORNINGS + 1)))
    shown_from = now - timedelta(days=days)
    shown_nights = [
        {
            "day": window["day"], "bed": window["bed"], "wake": window["wake"], "charge": round(window["charge"]),
            "reading": window["reading"], "hrv": window["hrv"], "rhr": window["rhr"], "sleep_min": window["sleep_min"],
            "hrv_baseline": round(window["hrv_baseline"]) if window["hrv_baseline"] else None,
            "rhr_baseline": round(window["rhr_baseline"]) if window["rhr_baseline"] else None,
            "summary": describe_night(window),
        }
        for window in windows if window["wake"] > shown_from
    ]
    since_yesterday = [activity for activity in activities if windows[-2]["wake"] <= activity["start"] <= now]
    largest = sorted(since_yesterday, key=lambda activity: activity["trimp"], reverse=True)[:2]
    reasons = [f"+{shown_nights[-1]['charge']} overnight: {shown_nights[-1]['summary']}"]
    reasons += [
        f"-{DRAIN_PER_TRIMP * activity['trimp']:.0f} {activity['name']}, {'today' if activity['start'].date() == today else 'yesterday'}"
        for activity in largest
    ]
    return {
        "now": now,
        "current": round(levels[now]),
        "morning": morning,
        "woke_at": windows[-1]["wake"],
        "usual": usual,
        "trend": morning - usual,
        "night": shown_nights[-1],
        "reasons": reasons,
        "series": [{"t": mark, "level": round(levels[mark], 1)} for mark in marks if shown_from <= mark <= now],
        "nights": shown_nights,
        "drains": [
            {
                "activity_id": activity["activity_id"], "name": activity["name"], "type": activity["type"],
                "start": activity["start"], "end": activity["start"] + timedelta(seconds=activity["seconds"] or 0),
                "trimp": round(activity["trimp"]), "cost": round(DRAIN_PER_TRIMP * activity["trimp"]),
            }
            for activity in activities if shown_from <= activity["start"] <= now
        ],
    }
