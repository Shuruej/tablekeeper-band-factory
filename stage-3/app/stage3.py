"""Stage 3 policy, reservation-history and recurring-series helpers."""

from datetime import date, datetime

from .seating import member_ids
from .time_rules import WEEKDAYS, parse_date, parse_hhmm
from .validation import ApiError, invalid, json_clone


def policy_zero(restaurant):
    return {
        "policy_version": 0,
        "slot_minutes": restaurant["slot_minutes"],
        "reservation_duration_minutes": restaurant["reservation_duration_minutes"],
        "cancellation_cutoff_minutes": restaurant["cancellation_cutoff_minutes"],
        "opening_hours": json_clone(restaurant["opening_hours"]),
        "capacities": {table["id"]: table["capacity"] for table in restaurant["tables"]},
    }


def accepted_terms(policy):
    return {key: json_clone(policy[key]) for key in (
        "policy_version", "slot_minutes", "reservation_duration_minutes",
        "cancellation_cutoff_minutes", "opening_hours", "capacities")}


def select_policy(data, restaurant, local_date):
    day = local_date if isinstance(local_date, date) else parse_date(str(local_date)[:10])
    best = policy_zero(restaurant)
    best_date = None
    for policy in data.get("policies", {}).get(restaurant["id"], []):
        effective = date.fromisoformat(policy["effective_from"])
        if effective <= day and (best_date is None or effective > best_date or
                                 (effective == best_date and policy["policy_version"] > best["policy_version"])):
            best, best_date = policy, effective
    return best


def policy_restaurant(restaurant, policy):
    result = json_clone(restaurant)
    for key in ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours"):
        result[key] = json_clone(policy[key])
    for table in result["tables"]:
        table["capacity"] = policy["capacities"][table["id"]]
    return result


def validate_policy(body, restaurant, version):
    required = ("effective_from", "slot_minutes", "reservation_duration_minutes",
                "cancellation_cutoff_minutes", "opening_hours", "capacities")
    if any(key not in body for key in required):
        invalid("incomplete policy")
    effective = parse_date(body["effective_from"])
    del effective
    for key, low, high in (("slot_minutes", 1, 1440),
                           ("reservation_duration_minutes", 1, 1440),
                           ("cancellation_cutoff_minutes", 0, 10080)):
        value = body[key]
        if type(value) is not int or not low <= value <= high:
            invalid("invalid policy")
    hours = body["opening_hours"]
    if not isinstance(hours, list):
        invalid("invalid policy")
    seen = set()
    for hour in hours:
        if not isinstance(hour, dict) or set(("weekday", "opens", "closes")) - set(hour):
            invalid("invalid policy")
        weekday = hour["weekday"]
        if weekday not in WEEKDAYS or weekday in seen or parse_hhmm(hour["opens"]) >= parse_hhmm(hour["closes"]):
            invalid("invalid policy")
        seen.add(weekday)
    capacities = body["capacities"]
    table_ids = {table["id"] for table in restaurant["tables"]}
    if not isinstance(capacities, dict) or set(capacities) != table_ids:
        invalid("invalid policy")
    if any(type(value) is not int or not 1 <= value <= 100 for value in capacities.values()):
        invalid("invalid policy")
    return {"effective_from": body["effective_from"], "slot_minutes": body["slot_minutes"],
            "reservation_duration_minutes": body["reservation_duration_minutes"],
            "cancellation_cutoff_minutes": body["cancellation_cutoff_minutes"],
            "opening_hours": json_clone(hours), "capacities": json_clone(capacities),
            "policy_version": version}


def history_changes(before, after, created=False):
    old_ids = [] if created else member_ids(before)
    new_ids = member_ids(after)
    changes = []
    if created or old_ids != new_ids:
        field = "table_ids" if len(old_ids) == 2 or len(new_ids) == 2 else "table_id"
        old = None if created else (old_ids if field == "table_ids" else old_ids[0])
        new = new_ids if field == "table_ids" else new_ids[0]
        changes.append({"field": field, "from": old, "to": json_clone(new)})
    for field in ("starts_at_local", "party_size"):
        old = None if created else before[field]
        if created or old != after[field]:
            changes.append({"field": field, "from": old, "to": after[field]})
    return changes


def append_history(data, reservation, event, changes, at=None):
    entries = data.setdefault("histories", {}).setdefault(reservation["reservation_id"], [])
    now = at or datetime.now().astimezone().isoformat(timespec="seconds")
    if entries and datetime.fromisoformat(now) < datetime.fromisoformat(entries[-1]["at"]):
        now = entries[-1]["at"]
    entry = {"seq": len(entries) + 1, "at": now, "event": event,
             "changes": json_clone(changes), "revision": reservation["revision"],
             "accepted_terms": json_clone(reservation["accepted_terms"])}
    entries.append(entry)
    return entry


def normalize_stage3(data, legacy=False):
    data.setdefault("policies", {rid: [] for rid in data["restaurants"]})
    data.setdefault("next_policy_version", {rid: 1 for rid in data["restaurants"]})
    data.setdefault("restaurant_revisions", {rid: 0 for rid in data["restaurants"]})
    data.setdefault("histories", {})
    data.setdefault("series", {})
    data.setdefault("series_order", [])
    data.setdefault("reservation_series", {})
    data.setdefault("next_series", 1)
    for rid, restaurant in data["restaurants"].items():
        restaurant.setdefault("manager_user_ids", [])
        data["policies"].setdefault(rid, [])
        data["next_policy_version"].setdefault(rid, 1)
        data["restaurant_revisions"].setdefault(rid, 0)
    for reservation in data["reservations"].values():
        reservation.setdefault("revision", 1)
        restaurant = data["restaurants"][reservation["restaurant_id"]]
        reservation.setdefault("accepted_terms", accepted_terms(policy_zero(restaurant)))
        if legacy and reservation["reservation_id"] not in data["histories"]:
            status = reservation["status"]
            reservation["revision"] = 1
            append_history(data, reservation, "created", history_changes({}, reservation, True),
                           reservation.get("created_at"))
            if status == "cancelled":
                reservation["revision"] = 2
                append_history(data, reservation, "cancelled", [], reservation.get("created_at"))
    return data
