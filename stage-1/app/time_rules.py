from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .validation import ApiError, LOCAL_RE, DATE_RE, TIME_RE, invalid

UTC = timezone.utc
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

def zone(name):
    try: return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError): invalid("invalid timezone")

def parse_local(text, zone_name):
    if not isinstance(text, str): raise ApiError(400, "malformed_request", "starts_at_local must be string")
    if not LOCAL_RE.fullmatch(text): invalid("invalid starts_at_local")
    try: naive = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError: invalid("invalid starts_at_local")
    z = zone(zone_name)
    aware = naive.replace(tzinfo=z, fold=0)
    if aware.astimezone(UTC).astimezone(z).replace(tzinfo=None) != naive:
        raise ApiError(422, "invalid_local_time")
    return aware

def parse_date(text):
    if not isinstance(text, str) or not DATE_RE.fullmatch(text): invalid("invalid date")
    try: return date.fromisoformat(text)
    except ValueError: invalid("invalid date")

def parse_hhmm(text):
    if not isinstance(text, str) or not TIME_RE.fullmatch(text): invalid("invalid time")
    return time.fromisoformat(text)

def absolute_end(start, minutes, z):
    return (start.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(z)

def overlaps(a_start, a_end, b_start, b_end):
    return a_start.astimezone(UTC) < b_end.astimezone(UTC) and b_start.astimezone(UTC) < a_end.astimezone(UTC)

def iso(dt):
    return dt.isoformat(timespec="seconds")

def opening_for(restaurant, local_date):
    wd = WEEKDAYS[local_date.weekday()]
    return next((x for x in restaurant["opening_hours"] if x["weekday"] == wd), None)

def validate_slot(restaurant, starts_text):
    start = parse_local(starts_text, restaurant["timezone"])
    opening = opening_for(restaurant, start.date())
    if opening is None: raise ApiError(422, "outside_opening_hours")
    opens_naive = datetime.combine(start.date(), parse_hhmm(opening["opens"]))
    closes_naive = datetime.combine(start.date(), parse_hhmm(opening["closes"]))
    naive = start.replace(tzinfo=None)
    if naive < opens_naive or naive >= closes_naive: raise ApiError(422, "outside_opening_hours")
    if (naive - opens_naive).total_seconds() % (restaurant["slot_minutes"] * 60):
        raise ApiError(422, "not_on_slot_grid")
    close = parse_local(closes_naive.strftime("%Y-%m-%dT%H:%M"), restaurant["timezone"])
    end = absolute_end(start, restaurant["reservation_duration_minutes"], start.tzinfo)
    if end.astimezone(UTC) > close.astimezone(UTC): raise ApiError(422, "outside_opening_hours")
    return start, end

def enumerate_slots(restaurant, local_date):
    opening = opening_for(restaurant, local_date)
    if opening is None: return []
    cur = datetime.combine(local_date, parse_hhmm(opening["opens"]))
    close_naive = datetime.combine(local_date, parse_hhmm(opening["closes"]))
    out = []
    while cur < close_naive:
        text = cur.strftime("%Y-%m-%dT%H:%M")
        try: out.append(validate_slot(restaurant, text))
        except ApiError as exc:
            if exc.code not in ("invalid_local_time", "outside_opening_hours"): raise
        cur += timedelta(minutes=restaurant["slot_minutes"])
    return out
