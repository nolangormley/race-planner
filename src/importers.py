"""Reads FIT and GPX files into the shapes the warehouse writes for a
Strava activity: a summary dict for dim_activity, streams keyed like
Strava's (time, heartrate, velocity_smooth, altitude, cadence, ...) and
laps. The activity id comes from a hash of the file, placed above every
Strava id, so importing the same file twice is a no-op and a Strava sync
can never collide with it.
"""
import hashlib
import io
import math
import re
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
import xml.etree.ElementTree as ElementTree

import fitdecode

IMPORT_ID_BASE = 2 ** 40
MOVING_SPEED_MPS = 0.5
SMOOTHING_SAMPLES = 5
STRAVA_TIME = "%Y-%m-%dT%H:%M:%SZ"
FIT_SPORT_TYPES = {
    "running": "Run", "cycling": "Ride", "walking": "Walk", "hiking": "Hike",
    "swimming": "Swim", "training": "Workout", "fitness_equipment": "Workout", "rowing": "Rowing",
}
GPX_TYPES = {"running": "Run", "run": "Run", "9": "Run", "cycling": "Ride", "biking": "Ride", "1": "Ride", "walking": "Walk", "hiking": "Hike"}


class UnreadableFile(ValueError):
    """The file is not a FIT or GPX activity we can read."""


def import_activity_id(data):
    """A stable id for the file's bytes in [2**40, 2**41)."""
    digest = hashlib.sha1(data).digest()
    return IMPORT_ID_BASE + int.from_bytes(digest[:8], "big") % IMPORT_ID_BASE


def number(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def fill_gaps(values):
    """Carries the last reading across missing samples; leading gaps take
    the first reading. None when nothing was recorded."""
    if all(value is None for value in values):
        return None
    first_value = next(value for value in values if value is not None)
    filled, last = [], first_value
    for value in values:
        if value is not None:
            last = value
        filled.append(last)
    return filled


def smooth(values, window=SMOOTHING_SAMPLES):
    half = window // 2
    smoothed = []
    for index in range(len(values)):
        neighbours = values[max(0, index - half):index + half + 1]
        smoothed.append(sum(neighbours) / len(neighbours))
    return smoothed


def increasing(offsets):
    """Indexes that keep the offsets strictly increasing: one sample per
    second, as the stream tables key on (activity, second)."""
    return [index for index in range(len(offsets)) if index == 0 or offsets[index] > offsets[index - 1]]


def speeds_from_distance(offsets, distances):
    speeds = [0.0] * len(offsets)
    for index in range(1, len(offsets)):
        seconds = offsets[index] - offsets[index - 1]
        speeds[index] = max(0.0, (distances[index] - distances[index - 1]) / seconds) if seconds > 0 else speeds[index - 1]
    if len(speeds) > 1:
        speeds[0] = speeds[1]
    return speeds


def elevation_gain(altitudes):
    if not altitudes:
        return 0.0
    smoothed = smooth(altitudes)
    return sum(max(0.0, later - earlier) for earlier, later in zip(smoothed, smoothed[1:]))


def moving_seconds(offsets, speeds):
    return sum(offsets[index] - offsets[index - 1] for index in range(1, len(offsets)) if speeds[index] > MOVING_SPEED_MPS)


def haversine_m(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    arc = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(arc))


def strava_time(moment):
    return moment.strftime(STRAVA_TIME)


def local_time(moment_utc, offset=None):
    """Wall-clock time: the file's own UTC offset when it has one, else the
    server's local zone (the athlete's, for a self-hosted install)."""
    if offset is not None:
        return moment_utc + offset
    return moment_utc.astimezone().replace(tzinfo=None)


def default_name(activity_type, local_start):
    """Strava's naming: "Morning Run", "Evening Ride", "Afternoon Trail Run"."""
    hour = local_start.hour
    part = "Morning" if 4 <= hour < 12 else "Afternoon" if 12 <= hour < 17 else "Evening" if 17 <= hour < 21 else "Night"
    return f"{part} {re.sub(r'(?<!^)(?=[A-Z])', ' ', activity_type)}"


def build_streams(offsets, heart_rates, speeds, altitudes, cadences, powers, temperatures):
    streams = {"time": {"data": offsets}, "velocity_smooth": {"data": speeds}, "moving": {"data": [speed > MOVING_SPEED_MPS for speed in speeds]}}
    for key, values in (("heartrate", heart_rates), ("altitude", altitudes), ("cadence", cadences), ("watts", powers), ("temp", temperatures)):
        filled = fill_gaps(values)
        if filled is not None:
            streams[key] = {"data": filled}
    return streams


def summary_from_samples(offsets, distances, speeds, heart_rates, altitudes, cadences):
    present_hr = [value for value in heart_rates if value is not None]
    present_cadence = [value for value in cadences if value is not None]
    moving = moving_seconds(offsets, speeds)
    distance = distances[-1] - distances[0] if distances else 0.0
    return {
        "distance": distance,
        "moving_time": int(moving),
        "elapsed_time": int(offsets[-1] - offsets[0]) if offsets else 0,
        "total_elevation_gain": elevation_gain([value for value in altitudes if value is not None]),
        "average_speed": distance / moving if moving else None,
        "max_speed": max(speeds) if speeds else None,
        "average_heartrate": sum(present_hr) / len(present_hr) if present_hr else None,
        "max_heartrate": max(present_hr) if present_hr else None,
        "average_cadence": sum(present_cadence) / len(present_cadence) if present_cadence else None,
    }


# ---- GPX ----

def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def child_text(element, name):
    for child in element.iter():
        if child is not element and local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def parse_gpx_time(text):
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def parse_gpx(data, filename):
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise UnreadableFile(f"Not valid GPX: {error}")
    points = []
    for point in root.iter():
        if local_name(point.tag) != "trkpt":
            continue
        time_text = child_text(point, "time")
        if not time_text:
            continue
        points.append({
            "time": parse_gpx_time(time_text),
            "lat": float(point.get("lat")), "lon": float(point.get("lon")),
            "ele": number(child_text(point, "ele")),
            "hr": number(child_text(point, "hr")),
            "cad": number(child_text(point, "cad")),
        })
    points.sort(key=lambda point: point["time"])
    if len(points) < 2:
        raise UnreadableFile("No track points with timestamps")

    start = points[0]["time"]
    offsets = [int(round((point["time"] - start).total_seconds())) for point in points]
    kept = increasing(offsets)
    points, offsets = [points[index] for index in kept], [offsets[index] for index in kept]
    if len(points) < 2:
        raise UnreadableFile("No track points with timestamps")

    distances, total = [0.0], 0.0
    for earlier, later in zip(points, points[1:]):
        total += haversine_m(earlier["lat"], earlier["lon"], later["lat"], later["lon"])
        distances.append(total)
    speeds = smooth(speeds_from_distance(offsets, distances))
    heart_rates = [point["hr"] for point in points]
    altitudes = [point["ele"] for point in points]
    cadences = [point["cad"] for point in points]

    start_utc = start.astimezone(timezone.utc)
    start_local = local_time(start_utc, start.utcoffset() or None)
    track = next((element for element in root.iter() if local_name(element.tag) == "trk"), root)
    activity_type = GPX_TYPES.get((child_text(track, "type") or "").lower(), "Run")
    summary = summary_from_samples(offsets, distances, speeds, heart_rates, altitudes, cadences)
    summary.update({
        "name": child_text(track, "name") or default_name(activity_type, start_local),
        "type": activity_type,
        "start_date": strava_time(start_utc),
        "start_date_local": strava_time(start_local),
        "device_name": "Imported GPX", "description": f"Imported from {filename}",
    })
    empty = [None] * len(offsets)
    return {"summary": summary, "streams": build_streams(offsets, heart_rates, speeds, altitudes, cadences, empty, empty), "laps": []}


# ---- FIT ----

def fit_messages(data):
    """(name, fields) for every data message in the file."""
    messages = []
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.WARN, error_handling=fitdecode.ErrorHandling.WARN) as reader:
            for frame in reader:
                if frame.frame_type == fitdecode.FIT_FRAME_DATA:
                    messages.append((frame.name, {field.name: field.value for field in frame.fields}))
    except fitdecode.FitError as error:
        raise UnreadableFile(f"Not a valid FIT file: {error}")
    return messages


def first(fields, *names):
    for name in names:
        if fields.get(name) is not None:
            return fields[name]
    return None


def as_utc(moment):
    if moment is None:
        return None
    if isinstance(moment, (int, float)):
        return datetime.fromtimestamp(fitdecode.FIT_UTC_REFERENCE + moment, timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def fit_activity_type(sport, sub_sport):
    sport, sub_sport = str(sport or "").lower(), str(sub_sport or "").lower()
    if sport == "running" and sub_sport == "trail":
        return "TrailRun"
    if sport == "cycling" and sub_sport in ("virtual_activity", "indoor_cycling"):
        return "VirtualRide"
    return FIT_SPORT_TYPES.get(sport, "Run")


def fit_utc_offset(activity):
    """The watch's UTC offset from the activity message, or None."""
    if activity.get("local_timestamp") is None or activity.get("timestamp") is None:
        return None
    local = as_utc(activity["local_timestamp"]).replace(tzinfo=None)
    return local - as_utc(activity["timestamp"]).replace(tzinfo=None)


def parse_fit_messages(messages, filename):
    """Builds the activity from decoded FIT messages: records become the
    streams, the session the summary (with fallbacks from the records), laps
    the laps."""
    records = sorted((fields for name, fields in messages if name == "record" and fields.get("timestamp") is not None), key=lambda fields: as_utc(fields["timestamp"]))
    if len(records) < 2:
        raise UnreadableFile("No records with timestamps")
    session = next((fields for name, fields in messages if name == "session"), {})
    activity = next((fields for name, fields in messages if name == "activity"), {})
    laps = [fields for name, fields in messages if name == "lap"]

    stamps = [as_utc(fields["timestamp"]) for fields in records]
    start = min(as_utc(session.get("start_time")) or stamps[0], stamps[0])
    offsets = [int(round((stamp - start).total_seconds())) for stamp in stamps]
    kept = increasing(offsets)
    records, stamps, offsets = [records[index] for index in kept], [stamps[index] for index in kept], [offsets[index] for index in kept]

    distances = fill_gaps([number(fields.get("distance")) for fields in records]) or [0.0] * len(records)
    recorded_speeds = [number(first(fields, "enhanced_speed", "speed")) for fields in records]
    speeds = smooth(fill_gaps(recorded_speeds) or speeds_from_distance(offsets, distances))
    heart_rates = [number(fields.get("heart_rate")) for fields in records]
    altitudes = [number(first(fields, "enhanced_altitude", "altitude")) for fields in records]
    cadences = [number(fields.get("cadence")) for fields in records]
    powers = [number(fields.get("power")) for fields in records]
    temperatures = [number(fields.get("temperature")) for fields in records]

    summary = summary_from_samples(offsets, distances, speeds, heart_rates, altitudes, cadences)
    for key, names in (
        ("distance", ("total_distance",)), ("moving_time", ("total_timer_time",)), ("elapsed_time", ("total_elapsed_time",)),
        ("total_elevation_gain", ("total_ascent",)), ("average_speed", ("enhanced_avg_speed", "avg_speed")),
        ("max_speed", ("enhanced_max_speed", "max_speed")), ("average_heartrate", ("avg_heart_rate",)),
        ("max_heartrate", ("max_heart_rate",)), ("average_cadence", ("avg_running_cadence", "avg_cadence")),
    ):
        value = number(first(session, *names))
        if value is not None:
            summary[key] = int(value) if key in ("moving_time", "elapsed_time") else value
    summary["calories"] = number(session.get("total_calories"))

    start_local = local_time(start, fit_utc_offset(activity))
    activity_type = fit_activity_type(session.get("sport"), session.get("sub_sport"))
    summary.update({
        "name": default_name(activity_type, start_local),
        "type": activity_type,
        "start_date": strava_time(start),
        "start_date_local": strava_time(start_local),
        "device_name": "Imported FIT", "description": f"Imported from {filename}",
    })
    return {"summary": summary, "streams": build_streams(offsets, heart_rates, speeds, altitudes, cadences, powers, temperatures), "laps": fit_laps(laps, stamps)}


def fit_laps(laps, stamps):
    """Laps in Strava's shape, with sample indexes found from the lap times."""
    result = []
    for fields in laps:
        lap_end = as_utc(fields.get("timestamp"))
        elapsed = number(fields.get("total_elapsed_time")) or 0.0
        lap_start = as_utc(fields.get("start_time")) or (lap_end - timedelta(seconds=elapsed) if lap_end else None)
        if lap_start is None:
            continue
        lap_end = lap_end or lap_start + timedelta(seconds=elapsed)
        start_index = min(bisect_left(stamps, lap_start), len(stamps) - 1)
        end_index = max(start_index, bisect_right(stamps, lap_end) - 1)
        position = len(result) + 1
        result.append({
            "lap_index": position, "name": f"Lap {position}",
            "start_index": start_index, "end_index": end_index,
            "elapsed_time": number(fields.get("total_elapsed_time")), "moving_time": number(fields.get("total_timer_time")),
            "distance": number(fields.get("total_distance")),
            "average_speed": number(first(fields, "enhanced_avg_speed", "avg_speed")), "max_speed": number(first(fields, "enhanced_max_speed", "max_speed")),
            "average_heartrate": number(fields.get("avg_heart_rate")), "max_heartrate": number(fields.get("max_heart_rate")),
            "average_cadence": number(first(fields, "avg_running_cadence", "avg_cadence")), "total_elevation_gain": number(fields.get("total_ascent")),
        })
    return result


def parse_fit(data, filename):
    return parse_fit_messages(fit_messages(data), filename)


def parse_file(filename, data):
    """The activity in a FIT or GPX file, with its id set from the bytes."""
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".fit":
        parsed = parse_fit(data, filename)
    elif suffix == ".gpx":
        parsed = parse_gpx(data, filename)
    else:
        raise UnreadableFile("Only .fit and .gpx files can be imported")
    parsed["summary"]["id"] = import_activity_id(data)
    return parsed
