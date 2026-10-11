"""A minimal GTFS-realtime reader: just enough protobuf wire format to pull TripUpdates out of a feed.

No protobuf library: the feed is walked field by field (varint, fixed32/64 and length-delimited), and only the
fields the departures board needs are kept. Unknown fields and extensions are skipped, so newer feeds still parse.

Field numbers are from gtfs-realtime.proto:
  FeedMessage    1 header, 2 entity
  FeedHeader     3 timestamp
  FeedEntity     1 id, 2 is_deleted, 3 trip_update
  TripUpdate     1 trip, 2 stop_time_update, 3 vehicle, 4 timestamp, 5 delay
  TripDescriptor 1 trip_id, 2 start_time, 3 start_date, 4 schedule_relationship, 5 route_id, 6 direction_id
  StopTimeUpdate 1 stop_sequence, 2 arrival, 3 departure, 4 stop_id, 5 schedule_relationship
  StopTimeEvent  1 delay, 2 time
  VehicleDescriptor 1 id, 2 label
"""

# TripDescriptor.schedule_relationship
SCHEDULED, ADDED, UNSCHEDULED, CANCELED = 0, 1, 2, 3
# StopTimeUpdate.schedule_relationship
STU_SCHEDULED, STU_SKIPPED, STU_NO_DATA = 0, 1, 2


class DecodeError(ValueError):
    pass


def _varint(buf, i):
    shift = result = 0
    while True:
        if i >= len(buf):
            raise DecodeError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise DecodeError("varint too long")


def fields(buf):
    """Yield (field_number, wire_type, value) for one message. Length-delimited values are memoryviews."""
    buf = memoryview(buf)
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            v, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
        else:  # groups (3/4) are not used by GTFS-rt
            raise DecodeError(f"unsupported wire type {wt}")
        if i > n:
            raise DecodeError("truncated field")
        yield num, wt, v


def signed(v):
    """int32/int64 fields encode negatives as 64-bit two's complement varints."""
    return v - (1 << 64) if v >= 1 << 63 else v


def _text(v):
    return bytes(v).decode("utf-8", "replace")


def _event(buf):
    out = {}
    for num, wt, v in fields(buf):
        if num == 1 and wt == 0:
            out["delay"] = signed(v)
        elif num == 2 and wt == 0:
            out["time"] = signed(v)
    return out


def _trip(buf):
    out = {"trip_id": "", "route_id": "", "start_date": "", "start_time": "", "rel": SCHEDULED, "direction_id": None}
    for num, wt, v in fields(buf):
        if wt == 2 and num in (1, 2, 3, 5):
            out[{1: "trip_id", 2: "start_time", 3: "start_date", 5: "route_id"}[num]] = _text(v)
        elif num == 4 and wt == 0:
            out["rel"] = v
        elif num == 6 and wt == 0:
            out["direction_id"] = v
    return out


def _stu(buf):
    out = {"seq": None, "stop_id": "", "arr": None, "dep": None, "rel": STU_SCHEDULED}
    for num, wt, v in fields(buf):
        if num == 1 and wt == 0:
            out["seq"] = v
        elif num == 2 and wt == 2:
            out["arr"] = _event(v)
        elif num == 3 and wt == 2:
            out["dep"] = _event(v)
        elif num == 4 and wt == 2:
            out["stop_id"] = _text(v)
        elif num == 5 and wt == 0:
            out["rel"] = v
    return out


def _trip_update(buf, keep):
    tu = {"trip": None, "stops": [], "delay": None, "timestamp": None, "vehicle": ""}
    raw_stops = []
    for num, wt, v in fields(buf):
        if num == 1 and wt == 2:
            tu["trip"] = _trip(v)
        elif num == 2 and wt == 2:
            raw_stops.append(v)
        elif num == 3 and wt == 2:
            for n2, w2, v2 in fields(v):
                if n2 == 2 and w2 == 2 or (n2 == 1 and w2 == 2 and not tu["vehicle"]):
                    tu["vehicle"] = _text(v2)
        elif num == 4 and wt == 0:
            tu["timestamp"] = v
        elif num == 5 and wt == 0:
            tu["delay"] = signed(v)
    if tu["trip"] is None:
        return None
    tu["stops"] = [_stu(s) for s in raw_stops]
    if keep is not None and not keep(tu):
        return None
    return tu


def parse_feed(data, keep=None):
    """Decode a FeedMessage. -> {"timestamp": int|None, "updates": [trip update dicts]}.

    keep(trip_update) -> bool drops updates the caller doesn't need, so a big city feed doesn't stay in memory.
    Each update: {"trip": {trip_id, route_id, start_date, start_time, rel, direction_id},
                  "stops": [{seq, stop_id, arr, dep, rel}], "delay", "timestamp", "vehicle"};
    arr/dep are None or {"time"?: epoch s, "delay"?: s}.
    """
    out = {"timestamp": None, "updates": []}
    for num, wt, v in fields(data):
        if num == 1 and wt == 2:
            for n2, w2, v2 in fields(v):
                if n2 == 3 and w2 == 0:
                    out["timestamp"] = v2
        elif num == 2 and wt == 2:
            deleted, tu_buf = False, None
            for n2, w2, v2 in fields(v):
                if n2 == 2 and w2 == 0:
                    deleted = bool(v2)
                elif n2 == 3 and w2 == 2:
                    tu_buf = v2
            if tu_buf is not None and not deleted:
                tu = _trip_update(tu_buf, keep)
                if tu:
                    out["updates"].append(tu)
    return out
