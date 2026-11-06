"""Race time predictions, pure: the time a runner would run if they raced today.

An effort is one row of the best efforts table, the fastest stretch of a
standard distance inside one run: {"distance_m", "seconds", "date", ...}.

1. Efforts. Only the last RECENT_DAYS count, so a fast mile from months ago
   does not set today's race. Without an effort that recent, those of the
   last OLD_DAYS count, and the range widens by RANGE_OLD.
2. The best effort for the race. Each effort's VDOT (Daniels, src.metrics)
   loses STRETCH_COST for every doubling or halving between its distance
   and the race's, and the effort that keeps the highest VDOT sets the
   time. A recent race is the hardest effort a runner makes, so it usually
   wins, and a race near the distance wins over a slightly faster one far
   from it. The stretch cost also makes scaling up a little slower than
   Daniels' tables: Vickers and Vertosick (below) found Riegel's scaling
   optimistic for recreational runners. Training runs are not all-out, so
   a runner who never races gets a cautious prediction rather than a
   flattering one.
3. Marathon. Daniels' and Riegel's scaling assume marathon training.
   Vickers and Vertosick (BMC Sports Sci Med Rehabil 8, 2016) found Riegel
   calibrated up to the half marathon, but at least 10 minutes optimistic
   for half the recreational marathoners they studied, with weekly mileage
   the strongest other predictor. So the marathon also takes Tanda's
   prediction from the last TRAINING_DAYS of training (J Hum Sport Exerc
   6(3), 2011, doi:10.4100/jhse.2011.63.05):
       pace (s/km) = 17.1 + 140.0 exp(-0.0053 K) + 0.55 P
   with K the mean weekly distance in km and P the mean training pace in
   s/km, from runs under HILLY_CLIMB so that hiked climbs do not count as
   pace. The prediction is the mean of the two, and the range spans both.
   Under MIN_TRAINING_KM a week, far outside Tanda's sample, it is left out.
4. Course. With a course profile, every time is multiplied by the course's
   mean grade cost (src.pacing.grade_factor, Minetti 2002): at an even
   effort, a hilly course takes that much longer than the flat.

STRETCH_COST and the ranges are rough, not fitted. The ranges say a
prediction is a band, wider for longer races and for older efforts.
"""
import math
from statistics import mean

from src.metrics import race_time_for_vdot, vdot_from_race
from src.pacing import grade_factor

RECENT_DAYS = 84
OLD_DAYS = 180
STRETCH_COST = 0.01         # share of VDOT per doubling of distance
TRAINING_DAYS = 56
HILLY_CLIMB = 20            # m of climb per km
MIN_TRAINING_KM = 10        # a week
MARATHON_M = 42195.0
SHORT_RACE_M = 10000.0      # up to this distance the range is RANGE_SHORT, above it RANGE_LONG
RANGE_SHORT = 0.025
RANGE_LONG = 0.035
RANGE_OLD = 0.03


def effort_vdot(effort, target_m):
    """The effort's VDOT, less STRETCH_COST per doubling between its distance and target_m."""
    stretch = abs(math.log2(target_m / effort["distance_m"]))
    return vdot_from_race(effort["distance_m"], effort["seconds"]) * (1 - STRETCH_COST * stretch)


def tanda_seconds(weekly_km, pace_s_per_km):
    """Tanda (2011): marathon time from 8 weeks of mean weekly distance and mean training pace."""
    return (17.1 + 140.0 * math.exp(-0.0053 * weekly_km) + 0.55 * pace_s_per_km) * MARATHON_M / 1000


def course_factor(profile):
    """Time on the course at an even effort over time on the flat: the mean
    grade cost of its points, which are evenly spaced. 1.0 without a profile."""
    return mean(grade_factor(point["grade"]) for point in profile) if profile else 1.0


def predict(efforts, target_m, old=False, training=None, profile=None):
    """The prediction for target_m.

    efforts: the window's best efforts, not empty. old: True when they are
    from the OLD_DAYS window. training: {"weekly_km", "pace_s_per_km"} over
    the last TRAINING_DAYS, or None. profile: the race's course profile
    (src.pacing.course_profile), or None.
    Returns seconds, low_seconds and high_seconds (over the course), from
    (the effort that set the time), training_seconds (Tanda's marathon, or
    None) and course_factor.
    """
    best = max(efforts, key=lambda effort: effort_vdot(effort, target_m))
    seconds = race_time_for_vdot(effort_vdot(best, target_m), target_m)
    spread = (RANGE_SHORT if target_m <= SHORT_RACE_M else RANGE_LONG) + (RANGE_OLD if old else 0.0)
    low, high = seconds * (1 - spread), seconds * (1 + spread)

    training_seconds = None
    if target_m == MARATHON_M and training and training["weekly_km"] >= MIN_TRAINING_KM:
        training_seconds = tanda_seconds(training["weekly_km"], training["pace_s_per_km"])
        middle = (seconds + training_seconds) / 2
        low = min(seconds, training_seconds, middle * (1 - spread))
        high = max(seconds, training_seconds, middle * (1 + spread))
        seconds = middle

    factor = course_factor(profile)
    return {
        "seconds": seconds * factor,
        "low_seconds": low * factor,
        "high_seconds": high * factor,
        "from": best,
        "training_seconds": training_seconds,
        "course_factor": factor,
    }
