"""Today's call, Go / Normal / Easy / Rest, from this morning's energy
level (src.energy) and its trend against the usual morning.

The morning level picks the band. A morning TREND_DROP or more under the
usual one makes the call one step more cautious, but never Rest on its
own: like Kiviniemi et al. (2007), a drop under the athlete's own recent
reference means an easy day. A hard session planned on an Easy or Rest
day gets a hint.
"""

GO, NORMAL, EASY, REST = "Go", "Normal", "Easy", "Rest"
GO_LEVEL = 90                # morning energy at or above which the call is Go
NORMAL_LEVEL = 75
EASY_LEVEL = 55              # under this the call is Rest
TREND_DROP = 10              # points under the usual morning that count as a falling trend
ADVICE = {
    GO: "Green light for quality work.",
    NORMAL: "Train as planned.",
    EASY: "Keep today easy and move quality to another day.",
    REST: "Rest, or a short walk. The body is asking for it.",
}
BADGES = {GO: "good", NORMAL: "accent", EASY: "warning", REST: "serious"}
HARD_SESSIONS = {"intervals", "tempo", "long run"}


def call(morning, trend):
    """The call for a morning level and its trend (morning minus the usual
    morning), with the advice, the badge class and the trend in words when
    it changed the call."""
    if morning >= GO_LEVEL:
        label = GO
    elif morning >= NORMAL_LEVEL:
        label = NORMAL
    elif morning >= EASY_LEVEL:
        label = EASY
    else:
        label = REST
    reasons = []
    if trend <= -TREND_DROP and label in (GO, NORMAL):
        label = NORMAL if label == GO else EASY
        reasons.append(f"woke {-trend} under your usual {morning - trend}")
    return {"label": label, "advice": ADVICE[label], "badge": BADGES[label], "reasons": reasons}


def session_hint(label, workout_type):
    """A nudge when a hard session is planned on an Easy or Rest day, else None."""
    if label not in (EASY, REST) or not workout_type or workout_type.lower() not in HARD_SESSIONS:
        return None
    swap = "a rest day" if label == REST else "an easy run"
    return f"Readiness says {label.lower()} today: consider swapping {workout_type} for {swap}."
