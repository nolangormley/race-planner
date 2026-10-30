"""Writes sample_run.fit, the FIT fixture for tests/test_importers.py.

A ten-minute run on 2026-10-25 from 11:00 UTC (07:00 on the watch, UTC-4):
two five-minute laps at 3.0 and 3.5 m/s, heart rate climbing from 140 to
160, one record a second. fitdecode only decodes, so this is a minimal
encoder; field numbers, base types and scales come from fitdecode's own
profile. Run it again only to change the fixture:

    python tests/fixtures/make_fit_fixture.py
"""
import io
import math
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitdecode
from fitdecode import profile

OUTPUT = Path(__file__).with_name("sample_run.fit")
START = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)
UTC_OFFSET = timedelta(hours=-4)
SECONDS = 600
LAP_SECONDS = 300
CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)
PACK_FORMATS = {1: "<B", 2: "<H", 4: "<I"}
MESSAGE_NUMBERS = {"file_id": 0, "session": 18, "lap": 19, "record": 20, "activity": 34}
ENUMS = {"file_activity": 4, "manufacturer_development": 255, "sport_running": 1, "sub_sport_generic": 0}


def fit_crc(data, crc=0):
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            low = CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ low ^ CRC_TABLE[nibble]
    return crc


def field_spec(message_name, field_name):
    message = profile.MESSAGE_TYPES[MESSAGE_NUMBERS[message_name]]
    for field_number, field in message.fields.items():
        if field.name == field_name:
            base = getattr(field.type, "base_type", field.type)
            return field_number, base.size, base.identifier, field.scale or 1, field.offset or 0
    raise KeyError(f"{message_name}.{field_name}")


def fit_time(moment):
    return int(moment.timestamp()) - fitdecode.FIT_UTC_REFERENCE


class Encoder:
    def __init__(self):
        self.records = io.BytesIO()
        self.local_numbers = {}

    def define(self, message_name, field_names):
        local_number = len(self.local_numbers)
        specs = [field_spec(message_name, name) for name in field_names]
        self.local_numbers[message_name] = (local_number, specs)
        content = struct.pack("<BBHB", 0, 0, MESSAGE_NUMBERS[message_name], len(specs))
        content += b"".join(struct.pack("<BBB", number, size, base) for number, size, base, _, _ in specs)
        self.records.write(bytes([0x40 | local_number]) + content)

    def write(self, message_name, values):
        """values are in profile units (metres, m/s, seconds); dates are
        already FIT timestamps."""
        local_number, specs = self.local_numbers[message_name]
        payload = bytes([local_number])
        for (_, size, _, scale, offset), value in zip(specs, values):
            payload += struct.pack(PACK_FORMATS[size], int(round((value + offset) * scale)))
        self.records.write(payload)

    def file_bytes(self):
        records = self.records.getvalue()
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(records), b".FIT")
        header += struct.pack("<H", fit_crc(header))
        body = header + records
        return body + struct.pack("<H", fit_crc(body))


def build():
    encoder = Encoder()
    encoder.define("file_id", ["type", "manufacturer", "time_created"])
    encoder.write("file_id", [ENUMS["file_activity"], ENUMS["manufacturer_development"], fit_time(START)])

    encoder.define("record", ["timestamp", "distance", "speed", "heart_rate", "altitude", "cadence"])
    distance, samples = 0.0, []
    for second in range(SECONDS + 1):
        speed = 3.0 if second < LAP_SECONDS else 3.5
        if second:
            distance += speed
        heart_rate = 140 + 20 * second / SECONDS
        altitude = 250 + 4 * math.sin(second / 60)
        samples.append((second, distance, speed, heart_rate, altitude))
        encoder.write("record", [fit_time(START + timedelta(seconds=second)), distance, speed, round(heart_rate), altitude, 85])

    encoder.define("lap", ["timestamp", "start_time", "total_elapsed_time", "total_timer_time", "total_distance", "avg_speed", "avg_heart_rate", "max_heart_rate"])
    for lap_start in range(0, SECONDS, LAP_SECONDS):
        lap = [sample for sample in samples if lap_start <= sample[0] <= lap_start + LAP_SECONDS]
        lap_distance = lap[-1][1] - lap[0][1]
        encoder.write("lap", [
            fit_time(START + timedelta(seconds=lap_start + LAP_SECONDS)), fit_time(START + timedelta(seconds=lap_start)),
            LAP_SECONDS, LAP_SECONDS, lap_distance, lap_distance / LAP_SECONDS,
            round(sum(sample[3] for sample in lap) / len(lap)), round(max(sample[3] for sample in lap)),
        ])

    end = START + timedelta(seconds=SECONDS)
    encoder.define("session", ["timestamp", "start_time", "sport", "sub_sport", "total_elapsed_time", "total_timer_time", "total_distance",
                               "avg_speed", "max_speed", "avg_heart_rate", "max_heart_rate", "total_ascent", "total_calories"])
    encoder.write("session", [
        fit_time(end), fit_time(START), ENUMS["sport_running"], ENUMS["sub_sport_generic"], SECONDS, SECONDS, distance,
        distance / SECONDS, 3.5, round(sum(sample[3] for sample in samples) / len(samples)), 160, 9, 130,
    ])

    encoder.define("activity", ["timestamp", "local_timestamp", "num_sessions", "total_timer_time"])
    encoder.write("activity", [fit_time(end), fit_time(end + UTC_OFFSET), 1, SECONDS])
    return encoder.file_bytes()


def check(data):
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE, error_handling=fitdecode.ErrorHandling.RAISE) as reader:
        return sum(1 for frame in reader if frame.frame_type == fitdecode.FIT_FRAME_DATA)


if __name__ == "__main__":
    data = build()
    messages = check(data)
    OUTPUT.write_bytes(data)
    print(f"wrote {OUTPUT} ({len(data)} bytes, {messages} data messages)")
