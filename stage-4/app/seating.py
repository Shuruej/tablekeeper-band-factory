"""Canonical seating choices shared by fixtures, mutations, availability and import."""

from .validation import ApiError, invalid, malformed


def member_ids(reservation):
    return reservation["table_ids"] if "table_ids" in reservation else [reservation["table_id"]]


def input_ids(body, existing=None):
    if "table_id" in body and "table_ids" in body:
        invalid("use table_id or table_ids")
    if "table_id" in body:
        if type(body["table_id"]) is not str:
            malformed("table_id must be a string")
        ids = [body["table_id"]]
    elif "table_ids" in body:
        ids = body["table_ids"]
        if type(ids) is not list or any(type(value) is not str for value in ids):
            malformed("table_ids must be an array of strings")
    elif existing is not None:
        ids = member_ids(existing)
    else:
        invalid("missing table selection")
    if len(ids) > 2:
        raise ApiError(422, "combination_not_allowed")
    if not ids or any(not value or len(value) > 64 for value in ids) or len(set(ids)) != len(ids):
        invalid("invalid table selection")
    return ids


def canonical_ids(restaurant, ids):
    tables = {table["id"]: table for table in restaurant["tables"]}
    if any(table_id not in tables for table_id in ids):
        raise ApiError(404, "not_found")
    if len(ids) == 1:
        return list(ids), tables[ids[0]]["capacity"]
    for declared in restaurant.get("combinable", []):
        if set(declared) == set(ids):
            return list(declared), sum(tables[table_id]["capacity"] for table_id in declared)
    raise ApiError(422, "combination_not_allowed")


def seating_fields(ids):
    fields = {"table_ids": list(ids)}
    if len(ids) == 1:
        fields["table_id"] = ids[0]
    return fields
