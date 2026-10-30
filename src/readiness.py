"""Daily readiness: a deterministic go / normal / easy / rest call from
last night's readings and today's form.

Each input that is present scores from -2 (clearly worse than usual) to
+1 (as good as or better than usual) and the total picks the label. With
no overnight reading there is no call: form on its own is already shown
as the form tile.
"""

GO, NORMAL, EASY, REST = "Go", "Normal", "Easy", "Rest"
ADVICE = {
    GO: "Green light for quality work.",
    NORMAL: "Train as planned.",
    EASY: "Keep today easy and move quality to another day.",
    REST: "Rest, or a short walk. The body is asking for it.",
}
BADGES = {GO: "good", NORMAL: "accent", EASY: "warning", REST: "serious"}
HARD_SESSIONS = {"intervals", "tempo", "long run"}


def hours_minutes(minutes):
    hours, rest = divmod(int(round(minutes)), 60)
    return f"{hours}h {rest:02d}m"


def hrv_points(hrv, baseline):
    if not hrv or not baseline:
        return 0, None
    ratio = hrv / baseline
    if ratio >= 1.0:
        return 1, "HRV at or above baseline"
    if ratio >= 0.9:
        return 0, None
    under = f"HRV {round((1 - ratio) * 100)}% under baseline"
    return (-1, under) if ratio >= 0.8 else (-2, under)


def resting_hr_points(rhr, usual):
    if not rhr or not usual:
        return 0, None
    delta = rhr - usual
    if delta <= 0:
        return 1, "resting heart rate at or under your usual"
    if delta <= 3:
        return 0, None
    higher = f"resting heart rate +{delta:.0f} bpm"
    return (-1, higher) if delta <= 6 else (-2, higher)


def sleep_points(minutes):
    if not minutes:
        return 0, None
    if minutes >= 420:
        return 1, f"{hours_minutes(minutes)} in bed"
    if minutes >= 360:
        return 0, None
    short = f"only {hours_minutes(minutes)} in bed"
    return (-1, short) if minutes >= 300 else (-2, short)


def form_points(tsb):
    if tsb is None:
        return 0, None
    if tsb > 5:
        return 1, f"form {tsb:+.0f}, fresh"
    if tsb >= -10:
        return 0, None
    if tsb >= -30:
        return -1, f"form {tsb:+.0f}, carrying load"
    return -2, f"form {tsb:+.0f}, overreaching"


def readiness(hrv=None, hrv_baseline=None, rhr=None, rhr_usual=None, sleep_min=None, tsb=None):
    """The call for today, or None without any overnight reading."""
    if not (hrv and hrv_baseline) and not (rhr and rhr_usual):
        return None
    parts = [hrv_points(hrv, hrv_baseline), resting_hr_points(rhr, rhr_usual), sleep_points(sleep_min), form_points(tsb)]
    total = sum(points for points, _ in parts)
    if total >= 2:
        label = GO
    elif total >= -1:
        label = NORMAL
    elif total >= -4:
        label = EASY
    else:
        label = REST
    return {
        "label": label,
        "score": total,
        "reasons": [reason for points, reason in parts if reason and points != 0],
        "advice": ADVICE[label],
        "badge": BADGES[label],
    }


def session_hint(label, workout_type):
    """A nudge when a hard session is planned on an Easy or Rest day, else None."""
    if label not in (EASY, REST) or not workout_type or workout_type.lower() not in HARD_SESSIONS:
        return None
    swap = "a rest day" if label == REST else "an easy run"
    return f"Readiness says {label.lower()} today: consider swapping {workout_type} for {swap}."
