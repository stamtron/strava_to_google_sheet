"""
Minimal reader for Garmin FIT activity files (no third-party dependency).

Decodes only what aerobic decoupling needs: the file type, the session summary
(sport, start, timer time, distance) and the per-record stream (timestamp,
heart rate, cumulative distance, speed). Everything else — developer fields,
arrays, strings, unknown messages — is skipped by size.

FIT layout (Garmin FIT SDK, "Flexible and Interoperable Data Transfer"):
a 12/14-byte header, then a stream of definition and data messages. A
definition message maps a local message type (0-15) to a global message number
and a field list; data messages carry values in that layout. Compressed
timestamp headers encode a 5-bit offset from the last full timestamp.

Input is untrusted (a user-supplied export), so every read is bounds-checked
and a malformed file raises `FitError` rather than reading past the buffer.
"""

import struct
from datetime import UTC, datetime, timedelta

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)

# Global message numbers and field definition numbers (FIT SDK profile).
MESG_FILE_ID = 0
MESG_SESSION = 18
MESG_RECORD = 20
FILE_TYPE_ACTIVITY = 4

_FILE_ID_FIELDS = {0: "type"}
_SESSION_FIELDS = {2: "start_time", 5: "sport", 6: "sub_sport", 7: "total_timer_time", 9: "total_distance"}
_RECORD_FIELDS = {253: "timestamp", 3: "heart_rate", 5: "distance", 6: "speed", 73: "enhanced_speed"}
_WANTED = {MESG_FILE_ID: _FILE_ID_FIELDS, MESG_SESSION: _SESSION_FIELDS, MESG_RECORD: _RECORD_FIELDS}

# FIT sport enum values used by callers.
SPORT_NAMES = {0: "generic", 1: "running", 2: "cycling", 5: "swimming", 11: "walking", 17: "hiking"}

# Base type number (low 5 bits) -> (struct code, size, invalid value).
_BASE_TYPES = {
    0: ("B", 1, 0xFF),        # enum
    1: ("b", 1, 0x7F),        # sint8
    2: ("B", 1, 0xFF),        # uint8
    3: ("h", 2, 0x7FFF),      # sint16
    4: ("H", 2, 0xFFFF),      # uint16
    5: ("i", 4, 0x7FFFFFFF),  # sint32
    6: ("I", 4, 0xFFFFFFFF),  # uint32
    10: ("B", 1, 0x00),       # uint8z
    11: ("H", 2, 0x0000),     # uint16z
    12: ("I", 4, 0x00000000), # uint32z
}


class FitError(ValueError):
    """Raised for anything that isn't a well-formed FIT file."""


def _header(data: bytes, offset: int) -> tuple[int, int]:
    """Return (records_start, records_end) for the FIT file at `offset`."""
    if len(data) - offset < 12:
        raise FitError("truncated header")
    header_size = data[offset]
    if header_size not in (12, 14) or data[offset + 8:offset + 12] != b".FIT":
        raise FitError("not a FIT file")
    (data_size,) = struct.unpack_from("<I", data, offset + 4)
    start = offset + header_size
    end = start + data_size
    if end > len(data):
        raise FitError("truncated data")
    return start, end


def read_fit(data: bytes, stop_unless_activity: bool = True) -> dict:
    """
    Decode a FIT file into {"file_type", "sessions", "records"}.

    `sessions` and `records` are lists of dicts keyed by the field names above,
    with raw FIT integer values (scaling is applied by `fit_activity_summary`).
    When `stop_unless_activity` is set, decoding stops as soon as the file_id
    shows a non-activity file (monitoring, settings…), which is most of a
    Garmin export, so bulk imports stay fast.
    """
    out = {"file_type": None, "sessions": [], "records": []}
    offset = 0
    # Chained FIT files (several files back to back, each with a 2-byte CRC).
    while True:
        start, end = _header(data, offset)
        if not _read_messages(data, start, end, out, stop_unless_activity):
            break
        offset = end + 2
        if len(data) - offset < 12:
            break
    return out


def _read_messages(data: bytes, pos: int, end: int, out: dict, stop_unless_activity: bool) -> bool:
    """Decode one FIT file's messages into `out`. False means stop reading."""
    definitions: dict[int, dict] = {}
    last_timestamp = None

    def need(n: int) -> None:
        if pos + n > end:
            raise FitError("message runs past end of data")

    while pos < end:
        need(1)
        header = data[pos]
        pos += 1
        compressed_ts = None

        if header & 0x80:  # compressed timestamp header
            local = (header >> 5) & 0x03
            ts_offset = header & 0x1F
            if last_timestamp is not None:
                base = last_timestamp & ~0x1F
                compressed_ts = base + ts_offset
                if ts_offset < (last_timestamp & 0x1F):
                    compressed_ts += 0x20
                last_timestamp = compressed_ts
            is_definition = False
        else:
            local = header & 0x0F
            is_definition = bool(header & 0x40)

        if is_definition:
            has_dev = bool(header & 0x20)
            need(5)
            arch = data[pos + 1]
            endian = ">" if arch == 1 else "<"
            (global_num,) = struct.unpack_from(endian + "H", data, pos + 2)
            n_fields = data[pos + 4]
            pos += 5
            need(n_fields * 3)
            fields = [tuple(data[pos + i * 3:pos + i * 3 + 3]) for i in range(n_fields)]
            pos += n_fields * 3
            dev_size = 0
            if has_dev:
                need(1)
                n_dev = data[pos]
                pos += 1
                need(n_dev * 3)
                dev_size = sum(data[pos + i * 3 + 1] for i in range(n_dev))
                pos += n_dev * 3
            definitions[local] = {
                "global": global_num,
                "endian": endian,
                "fields": fields,
                "dev_size": dev_size,
                "size": sum(f[1] for f in fields) + dev_size,
            }
            continue

        defn = definitions.get(local)
        if defn is None:
            raise FitError(f"data message for undefined local type {local}")
        need(defn["size"])
        wanted = _WANTED.get(defn["global"])
        if wanted is None:
            pos += defn["size"]
            continue

        values = {}
        field_pos = pos
        for num, size, base in defn["fields"]:
            name = wanted.get(num)
            spec = _BASE_TYPES.get(base & 0x1F)
            if name and spec and spec[1] == size:
                (value,) = struct.unpack_from(defn["endian"] + spec[0], data, field_pos)
                if value != spec[2]:
                    values[name] = value
            field_pos += size
        pos += defn["size"]

        if "timestamp" in values:
            last_timestamp = values["timestamp"]
        elif compressed_ts is not None:
            values["timestamp"] = compressed_ts

        if defn["global"] == MESG_FILE_ID:
            out["file_type"] = values.get("type")
            if stop_unless_activity and out["file_type"] != FILE_TYPE_ACTIVITY:
                return False
        elif defn["global"] == MESG_SESSION:
            out["sessions"].append(values)
        else:
            out["records"].append(values)
    return True


def fit_time(raw: int) -> datetime:
    """FIT timestamp (seconds since 1989-12-31 UTC) -> aware UTC datetime."""
    return FIT_EPOCH + timedelta(seconds=raw)


def fit_activity_summary(fit: dict) -> dict | None:
    """
    Scaled summary of a decoded activity: start (UTC), sport name, timer
    seconds, distance (m), plus the record stream as
    [{"t": unix seconds, "hr": bpm|None, "dist": m|None, "speed": m/s|None}].
    None when the file isn't an activity or has no session.
    """
    if fit.get("file_type") != FILE_TYPE_ACTIVITY or not fit.get("sessions"):
        return None
    session = fit["sessions"][0]
    if "start_time" not in session:
        return None
    start = fit_time(session["start_time"])
    records = []
    for r in fit.get("records", []):
        if "timestamp" not in r:
            continue
        speed = r.get("enhanced_speed", r.get("speed"))
        records.append({
            "t": fit_time(r["timestamp"]).timestamp(),
            "hr": r.get("heart_rate"),
            "dist": r["distance"] / 100 if "distance" in r else None,
            "speed": speed / 1000 if speed is not None else None,
        })
    return {
        "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sport": SPORT_NAMES.get(session.get("sport"), "other"),
        "timer_secs": session.get("total_timer_time", 0) / 1000,
        "distance_m": session.get("total_distance", 0) / 100,
        "records": records,
    }
