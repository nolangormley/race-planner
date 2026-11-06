"""What to work on: rule-based notes for the dashboard, pure. Each rule takes
plain values and returns one insight or None; src/api/services/insights.py
gathers the values for a user.

An insight is {"id", "tone", "label", "title", "text", "evidence"}. tone is
the badge colour (serious, warning, accent or good) and label the badge word,
so the colour never carries the meaning alone; text is one or two sentences
with the numbers and one thing to do (good news may have none); evidence says
what the rule looked at.
rank() puts serious first and keeps MAX_INSIGHTS. A rule stays silent on thin
data or a small signal: a false alarm costs more trust than a missed note.

Thresholds and their sources:
- Easy runs (easy_effort): the last 28 days' easy runs as the dashboard's
  efficiency card counts them (20 minutes or more at 60-82% of max heart
  rate), without detected tempo runs, treadmill runs and races; 4 or more of
  them. Detected intervals stay in: the detector also marks run-walk and
  stop-start runs. Too hard when their median average heart rate is more
  than EASY_MARGIN_BPM (3) over the top of the app's Easy zone, 70% of heart
  rate reserve (Karvonen, the zones of calculate_time_in_zones in
  src/pipeline.py), and under EASY_SHARE_LOW (60%) of all running time was
  in the Recovery and Easy zones, against the 80/20 guide (Seiler 2010,
  IJSPP 5:276). Good news when the median is inside the zone and that share
  is 70% or more.
- Efficiency (efficiency_terrain): the dashboard's easy-run efficiency
  factor (meters a minute per beat), the last 28 days against the 56 before,
  4 and 6 runs at least. Treadmill runs (no climb at all) are left out. A
  run is flat under 10 m of climb a km without "trail" in its name, hilly
  from 20 m a km or with "trail" in its name, rolling between. The factor
  uses raw speed, so a median 4% or more down while hilly runs are 40% or
  more of the recent ones, 20 points more than before, is the terrain. Flat
  runs alone (4 recent, 6 before) 6% down is a warning, 4% up good news.
  Paces are at the dashboard's reference heart rate, the middle of zone 2.
- Load (load_ramp): fitness (CTL) up more than RAMP_PER_WEEK (8) in a week
  is a warning; the usual guide for a sustainable ramp is 5 to 8 points a
  week of TSS-based fitness (Friel), and this app's load is Banister TRIMP,
  so the figure is rough. The acute:chronic ratio (ATL/CTL) over 1.5 is
  serious (Gabbett 2016, BJSM 50:273; the ratio's pitfalls in Impellizzeri
  2020, IJSPP 15:907 are why the cutoff is high). Both wait for
  LOAD_MIN_DAYS (84) of history, not the dashboard's 28: the 42-day average
  starts from zero, so for its first weeks it climbs fast and steady
  training reads as a ratio of 2 at day 28 and still over 1.5 at day 42.
- Recovery (recovery_trend): the COROS 7-night HRV mean 10% or more under
  the mean of the 28 nights before, with the resting heart rate 3 bpm or
  more over its own, from 5 or more recent and 20 or more earlier nights
  (HRV-guided monitoring as in Plews 2013, Sports Med 43:773).
- Plan (plan_slipping): under 60% of the last 14 days' planned sessions
  done, from 4 or more; done as the dashboard's week card counts it.
"""
from datetime import timedelta
from statistics import mean, median

METERS_PER_MILE = 1609.344
MAX_INSIGHTS = 4
TONE_ORDER = {"serious": 0, "warning": 1, "accent": 2, "good": 3}

EASY_MIN_RUNS = 4
EASY_ZONE_TOP = 0.70
EASY_MARGIN_BPM = 3
EASY_SHARE_LOW = 0.60
EASY_SHARE_GOOD = 0.70

EF_RECENT_DAYS = 28
EF_BASELINE_DAYS = 56
EF_MIN_RECENT = 4
EF_MIN_BASELINE = 6
FLAT_CLIMB = 10
HILLY_CLIMB = 20
EF_DIP = -0.04
HILLY_SHARE = 0.40
HILLY_SHARE_RISE = 0.20
FLAT_MIN_RECENT = 4
FLAT_MIN_BASELINE = 6
FLAT_DROP = -0.06
FLAT_RISE = 0.04
FLAT_STEADY = 0.02

RAMP_PER_WEEK = 8
ACWR_HIGH = 1.5
LOAD_MIN_DAYS = 84

HRV_DROP = 0.10
RHR_RISE = 3
NIGHTS_RECENT = 7
NIGHTS_BASELINE = 28
MIN_RECENT_NIGHTS = 5
MIN_BASELINE_NIGHTS = 20

PLAN_MIN_SESSIONS = 4
PLAN_DONE_FLOOR = 0.60


def insight(key, tone, label, title, text, evidence):
    return {"id": key, "tone": tone, "label": label, "title": title, "text": text, "evidence": evidence}


def rank(insights):
    """The insights found, serious first, at most MAX_INSIGHTS; rules of one
    tone keep the order they were given in."""
    found = [item for item in insights if item]
    return sorted(found, key=lambda item: TONE_ORDER[item["tone"]])[:MAX_INSIGHTS]


def clock(seconds):
    seconds = int(round(abs(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def pace_seconds(ef, hr, unit):
    """Seconds a mile (unit "mi") or a km that an efficiency factor buys at heart rate hr."""
    meters = METERS_PER_MILE if unit == "mi" else 1000.0
    return 60 * meters / (ef * hr)


def easy_ceiling(hr_max, hr_rest):
    """The top of the Easy zone in bpm: 70% of heart rate reserve."""
    return hr_rest + EASY_ZONE_TOP * (hr_max - hr_rest)


def terrain(run):
    """flat, rolling or hilly from the climb in m per km and the name."""
    if run["climb"] >= HILLY_CLIMB or "trail" in (run["name"] or "").lower():
        return "hilly"
    return "flat" if run["climb"] < FLAT_CLIMB else "rolling"


def easy_effort(runs, zone_seconds, hr_max, hr_rest):
    """runs: the last 28 days' easy runs, [{"avg_hr", "climb", "name"}];
    zone_seconds: time in the five zones over all the window's runs."""
    total = sum(zone_seconds)
    if len(runs) < EASY_MIN_RUNS or not total:
        return None
    ceiling = easy_ceiling(hr_max, hr_rest)
    typical = median(run["avg_hr"] for run in runs)
    share = (zone_seconds[0] + zone_seconds[1]) / total
    evidence = f"{len(runs)} easy runs in the last 4 weeks · max HR {hr_max:.0f}, resting {hr_rest:.0f}"
    if typical > ceiling + EASY_MARGIN_BPM and share < EASY_SHARE_LOW:
        climbs = ", and walk the steep climbs on trails," if any(terrain(run) == "hilly" for run in runs) else ""
        return insight(
            "easy-effort", "warning", "Too hard", "Easy runs are not easy",
            f"Your easy runs average {typical:.0f} bpm, over the top of your Easy zone at {ceiling:.0f}, and only"
            f" {share:.0%} of your running time was easy against the usual guide of about 80%."
            f" Slow down on easy days{climbs} to stay under {ceiling:.0f}.",
            evidence,
        )
    if typical <= ceiling and share >= EASY_SHARE_GOOD:
        return insight(
            "easy-effort", "good", "On track", "Easy days are easy",
            f"Your easy runs average {typical:.0f} bpm, inside your Easy zone, and {share:.0%} of your running time was easy."
            " Keep it that way: it is what lets the hard days count.",
            evidence,
        )
    return None


def efficiency_terrain(runs, today, hr_ref, unit):
    """runs: the dashboard's easy-run efficiency points, [{"day", "name", "ef",
    "climb"}] with climb in m per km. hr_ref is the dashboard's reference
    heart rate and unit "mi" or "km" for the paces."""
    outdoor = [run for run in runs if run["climb"] > 0]
    split = today - timedelta(days=EF_RECENT_DAYS)
    start = split - timedelta(days=EF_BASELINE_DAYS)
    recent = [run for run in outdoor if split < run["day"] <= today]
    before = [run for run in outdoor if start < run["day"] <= split]
    if len(recent) < EF_MIN_RECENT or len(before) < EF_MIN_BASELINE:
        return None

    flat_recent = [run["ef"] for run in recent if terrain(run) == "flat"]
    flat_before = [run["ef"] for run in before if terrain(run) == "flat"]
    flat_change = flat_delta = None
    if len(flat_recent) >= FLAT_MIN_RECENT and len(flat_before) >= FLAT_MIN_BASELINE:
        flat_change = median(flat_recent) / median(flat_before) - 1
        flat_delta = pace_seconds(median(flat_recent), hr_ref, unit) - pace_seconds(median(flat_before), hr_ref, unit)
    flat_evidence = f"{len(flat_recent)} flat easy runs in the last 4 weeks, {len(flat_before)} in the 8 weeks before"

    if flat_change is not None and flat_change <= FLAT_DROP:
        return insight(
            "efficiency", "warning", "Check", "Flat-run efficiency is down",
            f"On flat easy runs you are {clock(flat_delta)}/{unit} slower at {hr_ref:.0f} bpm than in the 8 weeks before."
            " Look at fatigue, heat, sleep or a coming illness before you add training.",
            flat_evidence,
        )

    change = median(run["ef"] for run in recent) / median(run["ef"] for run in before) - 1
    hilly = [run for run in recent if terrain(run) == "hilly"]
    hilly_share = len(hilly) / len(recent)
    hilly_share_before = sum(terrain(run) == "hilly" for run in before) / len(before)
    if change <= EF_DIP and hilly_share >= HILLY_SHARE and hilly_share - hilly_share_before >= HILLY_SHARE_RISE:
        if flat_change is None:
            flat_text = " To follow your aerobic fitness, run a flat easy run every couple of weeks and compare those."
        elif abs(flat_change) < FLAT_STEADY:
            flat_text = " On flat runs your efficiency held steady."
        else:
            flat_text = (f" On flat runs you are {clock(flat_delta)}/{unit} {'faster' if flat_delta < 0 else 'slower'}"
                         f" at {hr_ref:.0f} bpm than in the 8 weeks before.")
        return insight(
            "efficiency", "accent", "Terrain", "The dip is the terrain",
            f"Efficiency is down {abs(change):.0%} over the last 4 weeks, but {len(hilly)} of your {len(recent)} easy runs"
            f" were hilly or trail runs ({mean(run['climb'] for run in hilly):.0f} m of climb a km), and the number does not"
            f" allow for climbing.{flat_text}",
            f"{len(recent)} easy runs in the last 4 weeks, {len(before)} in the 8 weeks before",
        )

    if flat_change is not None and flat_change >= FLAT_RISE:
        return insight(
            "efficiency", "good", "Improving", "Aerobic fitness is up",
            f"On flat easy runs you are {clock(flat_delta)}/{unit} faster at {hr_ref:.0f} bpm than in the 8 weeks before:"
            " your aerobic base is growing.",
            flat_evidence,
        )
    return None


def load_ramp(ctl_now, ctl_week_ago, acwr, history_days):
    """Fitness (CTL) today and a week ago, today's acute:chronic ratio, and the
    days from the first activity to today."""
    if history_days < LOAD_MIN_DAYS:
        return None
    ramp = ctl_now - ctl_week_ago
    evidence = f"Fitness {ctl_now:.0f} today, {ctl_week_ago:.0f} a week ago · acute:chronic load {acwr:.2f}"
    if acwr > ACWR_HIGH:
        return insight(
            "load", "serious", "Load spike", "Training load jumped",
            f"Your load over the last week is {acwr:.1f} times your 6-week load. Jumps this big are when injuries"
            " tend to happen; hold this week's volume or ease off.",
            evidence,
        )
    if ramp > RAMP_PER_WEEK:
        return insight(
            "load", "warning", "Ramping fast", "Fitness is climbing fast",
            f"Fitness rose {ramp:.0f} points in a week. More than about {RAMP_PER_WEEK} a week is hard to absorb;"
            " keep next week's volume where it is.",
            evidence,
        )
    return None


def recovery_trend(nights, today):
    """nights: COROS readings, [{"day", "hrv", "rhr"}] with None where a night has no value."""
    recent_start = today - timedelta(days=NIGHTS_RECENT)
    baseline_start = recent_start - timedelta(days=NIGHTS_BASELINE)
    recent = [night for night in nights if recent_start < night["day"] <= today]
    before = [night for night in nights if baseline_start < night["day"] <= recent_start]
    hrv_recent = [night["hrv"] for night in recent if night["hrv"]]
    hrv_before = [night["hrv"] for night in before if night["hrv"]]
    rhr_recent = [night["rhr"] for night in recent if night["rhr"]]
    rhr_before = [night["rhr"] for night in before if night["rhr"]]
    if min(len(hrv_recent), len(rhr_recent)) < MIN_RECENT_NIGHTS or min(len(hrv_before), len(rhr_before)) < MIN_BASELINE_NIGHTS:
        return None
    hrv_change = mean(hrv_recent) / mean(hrv_before) - 1
    rhr_change = mean(rhr_recent) - mean(rhr_before)
    if hrv_change > -HRV_DROP or rhr_change < RHR_RISE:
        return None
    return insight(
        "recovery", "warning", "Recovery", "Recovery is lagging",
        f"Your HRV is {abs(hrv_change):.0%} under its 4-week average ({mean(hrv_recent):.0f} against {mean(hrv_before):.0f} ms)"
        f" and your resting heart rate is up {rhr_change:.0f} bpm. Together they often mean fatigue, short sleep or a"
        " coming illness; keep the next few days easy.",
        f"COROS nights: {len(hrv_recent)} in the last week, {len(hrv_before)} in the 4 weeks before",
    )


def plan_slipping(planned, done):
    """planned and done: the last 14 days' planned sessions, rest days left out."""
    if planned < PLAN_MIN_SESSIONS or done / planned >= PLAN_DONE_FLOOR:
        return None
    return insight(
        "plan", "accent", "Plan", "The plan is slipping",
        f"You did {done} of {planned} planned sessions in the last two weeks. If the plan asks for more than fits your"
        " weeks, build it again from your current volume on the calendar.",
        "Planned sessions of the last 14 days, done as the This week card counts them",
    )
