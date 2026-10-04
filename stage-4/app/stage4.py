"""Stage 4 closure and deterministic bounded-replanning helpers."""

from datetime import datetime

from .seating import member_ids
from .time_rules import overlaps
from .validation import ApiError, invalid, json_clone


def normalize_stage4(data):
    data.setdefault("closures", {rid: [] for rid in data["restaurants"]})
    data.setdefault("replans", {})
    data.setdefault("next_plan", 1)
    for rid in data["restaurants"]:
        data["closures"].setdefault(rid, [])
    return data


def parse_instant(value):
    if not isinstance(value, str):
        invalid("invalid closure interval")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        invalid("invalid closure interval")
    if instant.tzinfo is None:
        invalid("invalid closure interval")
    return instant


def closure_overlaps(closure, start, end):
    return overlaps(start, end, datetime.fromisoformat(closure["from"]), datetime.fromisoformat(closure["to"]))


def closed_members(data, restaurant_id, table_ids, start, end, proposed=None):
    closures = list(data.get("closures", {}).get(restaurant_id, []))
    if proposed is not None:
        closures.append(proposed)
    chosen = set(table_ids)
    return any(closure["table_id"] in chosen and closure_overlaps(closure, start, end)
               for closure in closures)


def option_catalog(restaurant):
    return [[table["id"]] for table in restaurant["tables"]] + [list(pair) for pair in restaurant.get("combinable", [])]


def option_capacity(reservation, ids):
    capacities = reservation["accepted_terms"]["capacities"]
    return sum(capacities[table_id] for table_id in ids)


def plan_reassignment(data, restaurant, closure):
    if len(restaurant["tables"]) > 6 or len(restaurant.get("combinable", [])) > 4:
        raise ApiError(422, "planning_limit")
    close_start, close_end = datetime.fromisoformat(closure["from"]), datetime.fromisoformat(closure["to"])
    considered = [res for res in data["reservations"].values()
                  if res["restaurant_id"] == restaurant["id"] and res["status"] == "confirmed"
                  and overlaps(datetime.fromisoformat(res["starts_at"]), datetime.fromisoformat(res["ends_at"]), close_start, close_end)]
    considered.sort(key=lambda item: item["reference"])
    if len(considered) > 6:
        raise ApiError(422, "planning_limit")
    considered_ids = {res["reservation_id"] for res in considered}
    fixed = [res for res in data["reservations"].values()
             if res["status"] == "confirmed" and res["restaurant_id"] == restaurant["id"]
             and res["reservation_id"] not in considered_ids]
    catalog = option_catalog(restaurant)
    choices = []
    for reservation in considered:
        start, end = datetime.fromisoformat(reservation["starts_at"]), datetime.fromisoformat(reservation["ends_at"])
        feasible = []
        for rank, ids in enumerate(catalog):
            capacity = option_capacity(reservation, ids)
            if capacity < reservation["party_size"] or closed_members(data, restaurant["id"], ids, start, end, closure):
                continue
            blocked = False
            for other in fixed:
                if set(ids) & set(member_ids(other)) and overlaps(start, end, datetime.fromisoformat(other["starts_at"]), datetime.fromisoformat(other["ends_at"])):
                    blocked = True
                    break
            if not blocked:
                feasible.append((rank, ids, capacity))
        if not feasible:
            raise ApiError(409, "no_feasible_plan")
        choices.append(feasible)

    best = None
    best_assignments = None

    def visit(index, assigned, moved, unused, ranks):
        nonlocal best, best_assignments
        if best is not None and (moved, unused) > best[:2]:
            return
        if index == len(considered):
            objective = (moved, unused, tuple(ranks))
            if best is None or objective < best:
                best = objective
                best_assignments = [(list(ids), changed) for ids, changed, _start, _end in assigned]
            return
        reservation = considered[index]
        start, end = datetime.fromisoformat(reservation["starts_at"]), datetime.fromisoformat(reservation["ends_at"])
        old_ids = member_ids(reservation)
        for rank, ids, capacity in choices[index]:
            conflict = any(set(ids) & set(other_ids) and overlaps(start, end, other_start, other_end)
                           for other_ids, _changed, other_start, other_end in assigned)
            if conflict:
                continue
            changed = ids != old_ids
            visit(index + 1, assigned + [(ids, changed, start, end)], moved + int(changed),
                  unused + capacity - reservation["party_size"], ranks + [rank])

    visit(0, [], 0, 0, [])
    if best is None:
        raise ApiError(409, "no_feasible_plan")
    assignments = [{"reservation_id": reservation["reservation_id"], "reference": reservation["reference"],
                    "table_ids": ids, "changed": changed}
                   for reservation, (ids, changed) in zip(considered, best_assignments)]
    return assignments, best[0], best[1]
