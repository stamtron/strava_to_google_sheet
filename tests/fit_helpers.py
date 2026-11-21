"""Tiny FIT encoder for tests: builds synthetic activity files byte by byte."""

import struct

from src.integrations.fit_file import FIT_EPOCH

UINT8, ENUM, UINT16, UINT32 = 0x02, 0x00, 0x84, 0x86
_FMT = {UINT8: "B", ENUM: "B", UINT16: "H", UINT32: "I"}
_SIZE = {UINT8: 1, ENUM: 1, UINT16: 2, UINT32: 4}

FILE_ID = [(0, ENUM)]
SESSION = [(2, UINT32), (5, ENUM), (7, UINT32), (9, UINT32)]
RECORD = [(253, UINT32), (3, UINT8), (5, UINT32), (6, UINT16)]
LENGTH = [(4, UINT32), (12, ENUM)]


def definition(local: int, global_num: int, fields: list[tuple[int, int]], dev_sizes: list[int] = ()) -> bytes:
    header = 0x40 | local | (0x20 if dev_sizes else 0)
    out = struct.pack("<BBBHB", header, 0, 0, global_num, len(fields))
    out += b"".join(bytes([num, _SIZE[base], base]) for num, base in fields)
    if dev_sizes:
        out += bytes([len(dev_sizes)]) + b"".join(bytes([i, size, 0]) for i, size in enumerate(dev_sizes))
    return out


def data(local: int, fields: list[tuple[int, int]], values: list[int], dev: bytes = b"") -> bytes:
    fmt = "<" + "".join(_FMT[base] for _num, base in fields)
    return bytes([local]) + struct.pack(fmt, *values) + dev


def compressed(local: int, ts: int, fields: list[tuple[int, int]], values: list[int]) -> bytes:
    fmt = "<" + "".join(_FMT[base] for _num, base in fields)
    return bytes([0x80 | (local << 5) | (ts & 0x1F)]) + struct.pack(fmt, *values)


def wrap(body: bytes) -> bytes:
    """14-byte header + messages + (unchecked) CRC."""
    header = struct.pack("<BBHI4sH", 14, 0x20, 2100, len(body), b".FIT", 0)
    return header + body + b"\x00\x00"


def fit_ts(iso: str) -> int:
    from datetime import datetime

    return int((datetime.fromisoformat(iso) - FIT_EPOCH).total_seconds())


def activity_fit(
    start_iso: str = "2026-03-01T07:00:00+00:00",
    sport: int = 1,
    seconds: int = 4200,
    speed_ms: float = 3.5,
    hr: int = 145,
    hr_drift_per_s: float = 0.0,
    file_type: int = 4,
    session_hr: int | None = None,
    session_power: int | None = None,
    pause_after_s: int | None = None,
    pause_s: int = 0,
    lengths: list[tuple[int, int]] = (),
) -> bytes:
    """
    An activity with one record per second at constant speed. `session_hr` /
    `session_power` add the session avg_heart_rate (16) / avg_power (20) fields.
    `pause_after_s` / `pause_s` stand still for `pause_s` seconds (timer
    running) after that many seconds. `lengths` adds pool length messages as
    (timer seconds, length_type) pairs.
    """
    t0 = fit_ts(start_iso)
    body = definition(0, 0, FILE_ID) + data(0, FILE_ID, [file_type])
    body += definition(2, 20, RECORD)
    paused = pause_s if pause_after_s is not None else 0
    for i in range(seconds + 1):
        heart = min(254, round(hr + hr_drift_per_s * i))
        moved = i if pause_after_s is None else min(i, pause_after_s) + max(0, i - pause_after_s - pause_s)
        speed = 0 if pause_after_s is not None and pause_after_s < i <= pause_after_s + pause_s else speed_ms
        body += data(2, RECORD, [t0 + i, heart, round(moved * speed_ms * 100), round(speed * 1000)])
    if lengths:
        body += definition(3, 101, LENGTH)
        body += b"".join(data(3, LENGTH, [secs * 1000, kind]) for secs, kind in lengths)
    total_m = (seconds - paused) * speed_ms
    session = list(SESSION)
    values = [t0, sport, seconds * 1000, round(total_m * 100)]
    if session_hr is not None:
        session.append((16, UINT8))
        values.append(session_hr)
    if session_power is not None:
        session.append((20, UINT16))
        values.append(session_power)
    body += definition(1, 18, session) + data(1, session, values)
    return wrap(body)
