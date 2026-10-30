"""Fixtures shared by the unit tests."""
from datetime import timedelta

import pytest

GPX_HEADER = ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="tests" xmlns="http://www.topografix.com/GPX/1/1" '
              'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">')
METERS_PER_DEGREE_LATITUDE = 111_194.93


@pytest.fixture
def make_gpx():
    """Builds GPX bytes for a straight run north: one point every spacing_s
    seconds at speed_mps from start (an aware UTC datetime), with heart rate
    and cadence in Garmin's TrackPointExtension."""
    def build(start, points=200, spacing_s=3, speed_mps=3.2, name=None):
        latitude_step = speed_mps * spacing_s / METERS_PER_DEGREE_LATITUDE
        rows = []
        for index in range(points):
            moment = start + timedelta(seconds=index * spacing_s)
            rows.append(
                f'<trkpt lat="{40.44 + index * latitude_step:.7f}" lon="-79.9959"><ele>{230 + index * 0.1:.1f}</ele>'
                f'<time>{moment.strftime("%Y-%m-%dT%H:%M:%SZ")}</time><extensions><gpxtpx:TrackPointExtension>'
                f'<gpxtpx:hr>{150 + index % 5}</gpxtpx:hr><gpxtpx:cad>86</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>'
            )
        title = f"<name>{name}</name>" if name else ""
        return (GPX_HEADER + f"<trk>{title}<type>running</type><trkseg>" + "".join(rows) + "</trkseg></trk></gpx>").encode()
    return build
