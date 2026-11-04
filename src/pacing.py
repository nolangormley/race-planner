"""Race-day pacing: split targets for a goal time, even or negative, with
effort held level over a course's hills.

Each split gets a relative pace from the strategy (a 2% ramp for a
negative split) times the grade cost of its ground (Minetti et al. 2002,
the relation grade-adjusted pace is built on). The paces are then scaled
so the splits add up to the goal exactly, and the split times rounded to
whole seconds. parse_goal reads a typed goal for the race distance.
parse_gpx_course turns a course GPX into the per-100 m profile those costs
read.
"""
import itertools
import math
import xml.etree.ElementTree as ElementTree

METERS_PER_MILE = 1609.344
EARTH_RADIUS_M = 6371008.8
PROFILE_STEP_M = 100.0
SMOOTH_POINTS = 5            # moving average over 500 m of profile
NEGATIVE_SPLIT = 0.02        # second half this much faster than the first
LENGTH_TOLERANCE = 0.03
MAX_GRADE = 0.20
STRATEGIES = ("even", "negative")
# Goal paces a runner can hold, in seconds per mile. 3:30/mi is faster than
# every world record from the mile up (the mile record is 3:43/mi), and
# 20:00/mi is a walk at 3 mph.
GOAL_PACE_RANGE = (210.0, 1200.0)


def parse_clock(text):
    """'1:45:00', '45:00' or '1:45' as its numbers: [1, 45, 0], [45, 0] or
    [1, 45]. None when the text is not two or three whole numbers with the
    numbers after the first under 60."""
    if not text:
        return None
    parts = [part.strip() for part in text.split(":")]
    if len(parts) not in (2, 3) or not all(part.isdecimal() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if any(number >= 60 for number in numbers[1:]):
        return None
    return numbers


def goal_range(distance_m):
    """The fastest and the slowest goal for distance_m, in whole seconds."""
    miles = distance_m / METERS_PER_MILE
    return math.ceil(GOAL_PACE_RANGE[0] * miles), math.floor(GOAL_PACE_RANGE[1] * miles)


def parse_goal(text, distance_m):
    """A goal time for a race of distance_m, in seconds. None when the text
    is not a clock time or when no reading of it is in goal_range.

    Three numbers are h:mm:ss. Two numbers are m:ss when that time is in
    the range ('45:00' for a 10K), else h:mm ('1:45' for a half marathon).
    The two readings differ by a factor of 60 and the ends of the range by
    less than 6, so only one reading can be in the range.
    """
    numbers = parse_clock(text)
    if numbers is None:
        return None
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
        readings = [hours * 3600 + minutes * 60 + seconds]
    else:
        first, second = numbers
        readings = [first * 60 + second, first * 3600 + second * 60]
    fastest, slowest = goal_range(distance_m)
    return next((reading for reading in readings if fastest <= reading <= slowest), None)


def format_clock(seconds):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def grade_factor(grade):
    """Running cost on a gradient relative to flat ground.

    Minetti et al. (2002), J. Appl. Physiol. 93:1039-1046, fitted the energy
    cost of running at gradient i as
        Cr(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6 J/kg/m,
    so Cr(i) / 3.6 is how much more (or less) a metre costs than on the
    flat, which at a steady effort is how much slower (or faster) the pace
    is. Gradients are clipped to +/-20%, the range with running data.
    Downhill gives back half the saving the equation predicts: braking and
    eccentric load keep runners from cashing it all in.
    """
    gradient = max(-MAX_GRADE, min(MAX_GRADE, grade))
    cost = (155.4 * gradient ** 5 - 30.4 * gradient ** 4 - 43.3 * gradient ** 3
            + 46.3 * gradient ** 2 + 19.5 * gradient + 3.6)
    factor = cost / 3.6
    if factor < 1.0:
        factor = 1.0 - (1.0 - factor) / 2
    return factor


def split_bounds(distance_m, split_m):
    """[(start, end), ...] covering the distance; the last one may be short."""
    bounds = []
    start = 0.0
    while distance_m - start > 1.0:
        end = min(distance_m, start + split_m)
        bounds.append((start, end))
        start = end
    return bounds


def split_profile(profile, start_m, end_m):
    """Mean grade, mean grade cost and climb over one split. A profile point
    stands for the stretch of PROFILE_STEP_M ending at its distance, so the
    averages are weighted by how much of each stretch falls in the split."""
    if not profile:
        return 0.0, 1.0, 0.0
    total = grade_sum = factor_sum = climb = 0.0
    previous_m = 0.0
    for point in profile:
        segment_start, segment_end = previous_m, point["distance_m"]
        previous_m = segment_end
        overlap = min(end_m, segment_end) - max(start_m, segment_start)
        if overlap <= 0:
            continue
        total += overlap
        grade_sum += point["grade"] * overlap
        factor_sum += grade_factor(point["grade"]) * overlap
        if point["grade"] > 0:
            climb += point["grade"] * overlap
    if not total:
        return 0.0, 1.0, 0.0
    return grade_sum / total, factor_sum / total, climb


def whole_second_splits(planned, full):
    """Split times in whole seconds for the planned ones, which add up to
    the goal. full tells which splits have the full split length.

    Each split ends on the planned clock rounded to the second, so the
    clock ends on the goal. This rounding can give a full split one second
    more than a full split that is planned slower. The full splits then get
    the same times again, in the order of their planned times, so a faster
    plan never shows more time. Each split stays within a second of its
    planned time.
    """
    clocks = [round(clock) for clock in itertools.accumulate(planned)]
    seconds = [later - earlier for earlier, later in zip([0] + clocks, clocks)]
    indices = [ii for ii, is_full in enumerate(full) if is_full]
    # Splits with planned times equal to the millisecond keep the times that rounding gave them.
    by_plan = sorted(indices, key=lambda ii: (round(planned[ii], 3), seconds[ii]))
    for ii, value in zip(by_plan, sorted(seconds[ii] for ii in indices)):
        seconds[ii] = value
    return seconds


def plan_splits(distance_m, goal_seconds, split_m, strategy="even", profile=None):
    """Splits for a race: each with its grade, target pace (seconds per
    kilometre and per mile), split time and cumulative time.

    strategy 'even' holds one effort; 'negative' ramps the relative pace
    from 1 + NEGATIVE_SPLIT down to 1 - NEGATIVE_SPLIT across the race, so
    the second half runs about 2% faster than the first. With a profile
    (from course_profile) each split's pace also carries the grade cost of
    its ground, which holds the effort level over hills. Split and
    cumulative times are whole seconds (whole_second_splits) and the last
    cumulative time is the goal exactly. A split of full length shows the
    pace of its whole-second time, so its pace and split time read the
    same. A short last split shows the planned pace, because over so short
    a distance one second changes the pace by many seconds.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {', '.join(STRATEGIES)}")
    if distance_m <= 0 or goal_seconds <= 0 or split_m <= 0:
        raise ValueError("distance, goal and split length must be positive")
    rows = []
    for start, end in split_bounds(distance_m, split_m):
        midpoint = (start + end) / 2 / distance_m
        shape = 1.0 + NEGATIVE_SPLIT * (1.0 - 2.0 * midpoint) if strategy == "negative" else 1.0
        grade, factor, climb = split_profile(profile, start, end)
        rows.append({"start_m": start, "end_m": end, "length_m": end - start, "grade": grade, "climb_m": climb, "relative": shape * factor})

    seconds_per_metre = goal_seconds / sum(row["relative"] * row["length_m"] for row in rows)
    paces = [row.pop("relative") * seconds_per_metre for row in rows]
    full = [row["length_m"] > split_m - 1.0 for row in rows]
    split_times = whole_second_splits([pace * row["length_m"] for pace, row in zip(paces, rows)], full)
    cumulative = 0
    for ii, row in enumerate(rows):
        pace = split_times[ii] / row["length_m"] if full[ii] else paces[ii]
        cumulative += split_times[ii]
        row.update({
            "index": ii + 1,
            "seconds_per_km": pace * 1000.0,
            "seconds_per_mile": pace * METERS_PER_MILE,
            "split_seconds": split_times[ii],
            "cumulative_seconds": cumulative,
        })
    return rows


# ---- Course files ----

def haversine_m(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    chord = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(chord))


def gpx_points(text):
    """(lat, lon, ele) for every track or route point with an elevation."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ValueError(f"Not a GPX file: {error}")
    points = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] not in ("trkpt", "rtept"):
            continue
        elevation = next((child.text for child in element if child.tag.rsplit("}", 1)[-1] == "ele"), None)
        if elevation is None:
            continue
        try:
            points.append((float(element.get("lat")), float(element.get("lon")), float(elevation)))
        except (TypeError, ValueError):
            continue
    return points


def smooth(values, window=SMOOTH_POINTS):
    half = window // 2
    return [sum(values[max(0, index - half):index + half + 1]) / len(values[max(0, index - half):index + half + 1]) for index in range(len(values))]


def parse_gpx_course(text):
    """A course GPX as {"length_m", "ascent_m", "descent_m", "points"}, the
    points one per PROFILE_STEP_M with distance_m, elevation_m (smoothed)
    and grade. Raises ValueError for anything that is not a course with
    elevation."""
    raw = gpx_points(text)
    if len(raw) < 2:
        raise ValueError("The GPX has no track with elevation data.")
    distances = [0.0]
    for (lat1, lon1, _), (lat2, lon2, _) in zip(raw, raw[1:]):
        distances.append(distances[-1] + haversine_m(lat1, lon1, lat2, lon2))
    length = distances[-1]
    if length < PROFILE_STEP_M * 2:
        raise ValueError("The GPX track is too short to be a course.")

    # Resample elevation every PROFILE_STEP_M, ending on the course length.
    sample_distances = [step * PROFILE_STEP_M for step in range(1, int(length // PROFILE_STEP_M) + 1)]
    if length - sample_distances[-1] > 1.0:
        sample_distances.append(length)
    elevations = []
    cursor = 0
    for target in sample_distances:
        while cursor < len(distances) - 2 and distances[cursor + 1] < target:
            cursor += 1
        span = distances[cursor + 1] - distances[cursor]
        share = (target - distances[cursor]) / span if span else 0.0
        elevations.append(raw[cursor][2] + share * (raw[cursor + 1][2] - raw[cursor][2]))
    elevations = smooth(elevations)

    points = []
    previous_distance, previous_elevation = 0.0, elevations[0]
    ascent = descent = 0.0
    for distance, elevation in zip(sample_distances, elevations):
        run = distance - previous_distance
        rise = elevation - previous_elevation
        grade = max(-MAX_GRADE, min(MAX_GRADE, rise / run)) if run and points else 0.0
        points.append({"distance_m": distance, "elevation_m": elevation, "grade": grade})
        if points[-1] is not points[0]:
            ascent += max(0.0, rise)
            descent += max(0.0, -rise)
        previous_distance, previous_elevation = distance, elevation
    return {"length_m": length, "ascent_m": ascent, "descent_m": descent, "points": points}


def course_profile(course, distance_m):
    """The course points stretched to the race distance so splits line up,
    and a warning when the file's length is off by more than
    LENGTH_TOLERANCE."""
    scale = distance_m / course["length_m"]
    points = [
        {"distance_m": point["distance_m"] * scale, "elevation_m": point["elevation_m"], "grade": point["grade"] / scale}
        for point in course["points"]
    ]
    warnings = []
    if abs(1.0 - scale) > LENGTH_TOLERANCE:
        warnings.append(
            f"The course file measures {course['length_m'] / 1000:.2f} km but the race is {distance_m / 1000:.2f} km; "
            "the profile was stretched to fit."
        )
    return points, warnings
