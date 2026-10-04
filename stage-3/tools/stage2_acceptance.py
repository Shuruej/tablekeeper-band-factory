"""Focused Stage 2 API/upgrade contract checks; keeps credentials and exports in memory."""

import argparse
import concurrent.futures
import copy
import json
import threading
import urllib.error
import urllib.parse
import urllib.request


HOURS = [{"weekday": day, "opens": "18:00", "closes": "23:00"}
         for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
FIXTURE = {
    "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
    "restaurants": [{"id": "r_arden", "name": "The Arden Room", "timezone": "UTC", "slot_minutes": 30,
                     "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
                     "opening_hours": HOURS,
                     "tables": [{"id": "t_1", "label": "Window", "capacity": 2},
                                {"id": "t_2", "label": "Garden", "capacity": 2},
                                {"id": "t_3", "label": "Hearth", "capacity": 4}],
                     "combinable": [["t_2", "t_1"], ["t_2", "t_3"]]}],
    "reservations": [],
}


def request(base, method, path, body=None, token=None, key=None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    raw = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=raw, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read()
        return response.status, json.loads(payload) if payload else None


def expect(actual, status, code=None):
    assert actual[0] == status, (status, actual)
    if code:
        assert actual[1]["error"]["code"] == code, (code, actual)
    return actual[1]


def login(base):
    return expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]


def booking(ids, time="18:00", size=4):
    return {"restaurant_id": "r_arden", "table_ids": ids, "starts_at_local": "2035-01-01T" + time, "party_size": size}


def run(stage2, stage1):
    checks = 0
    def check(actual, status, code=None):
        nonlocal checks
        checks += 1
        return expect(actual, status, code)

    check(request(stage2, "POST", "/_test/reset", FIXTURE), 204)
    token = login(stage2)
    availability = check(request(stage2, "GET", "/availability?restaurant_id=r_arden&date=2035-01-01&party_size=4"), 200)
    slot = next(value for value in availability["slots"] if value["starts_at_local"].endswith("18:00"))
    assert slot["available_table_ids"] == ["t_3"]
    assert slot["available_options"] == [
        {"table_ids": ["t_3"], "capacity": 4},
        {"table_ids": ["t_2", "t_1"], "capacity": 4},
        {"table_ids": ["t_2", "t_3"], "capacity": 6},
    ]
    pair_body = booking(["t_1", "t_2"])
    pair = check(request(stage2, "POST", "/reservations", pair_body, token, "pair"), 201)
    assert pair["table_ids"] == ["t_2", "t_1"] and "table_id" not in pair
    assert check(request(stage2, "POST", "/reservations", pair_body, token, "pair"), 200) == pair
    check(request(stage2, "POST", "/reservations", booking(["t_1"], size=2), token, "occupied-single"), 409, "table_unavailable")
    check(request(stage2, "POST", "/reservations", booking(["t_2", "t_3"]), token, "occupied-pair"), 409, "table_unavailable")
    check(request(stage2, "POST", "/reservations", booking(["t_1", "t_3"]), token, "undeclared"), 422, "combination_not_allowed")
    check(request(stage2, "POST", "/reservations", booking(["t_1", "t_2", "t_3"]), token, "triple"), 422, "combination_not_allowed")
    check(request(stage2, "POST", "/reservations", booking(["t_1", "t_1"]), token, "duplicate"), 422, "validation_failed")
    check(request(stage2, "POST", "/reservations", {**pair_body, "table_id": "t_1"}, token, "both"), 422, "validation_failed")
    check(request(stage2, "POST", "/reservations", booking(["t_1", "t_2"], "19:00", 5), token, "capacity"), 422, "party_exceeds_capacity")
    single = check(request(stage2, "POST", "/reservations", booking(["t_1"], "19:00", 2), token, "edge"), 201)
    assert single["table_ids"] == ["t_1"] and single["table_id"] == "t_1"
    amended = check(request(stage2, "PATCH", "/reservations/" + pair["reference"], {"table_id": "t_3"}, token), 200)
    assert amended["table_ids"] == ["t_3"] and amended["table_id"] == "t_3"
    moved_pair = check(request(stage2, "PATCH", "/reservations/" + pair["reference"], {"table_ids": ["t_1", "t_2"]}, token), 200)
    assert moved_pair["table_ids"] == ["t_2", "t_1"] and "table_id" not in moved_pair
    check(request(stage2, "PATCH", "/reservations/" + pair["reference"], {"table_id": "t_1", "table_ids": ["t_2"]}, token), 422, "validation_failed")
    after = check(request(stage2, "GET", "/reservations/" + pair["reference"], token=token), 200)
    assert after == moved_pair
    cancelled = check(request(stage2, "POST", "/reservations/" + pair["reference"] + "/cancel", token=token), 200)
    assert cancelled["status"] == "cancelled"
    check(request(stage2, "POST", "/reservations", booking(["t_1", "t_2"]), token, "after-cancel"), 201)

    check(request(stage2, "POST", "/_test/reset", FIXTURE), 204)
    token = login(stage2)
    left = check(request(stage2, "POST", "/reservations", booking(["t_1"], "20:00", 2), token, "left"), 201)
    right = check(request(stage2, "POST", "/reservations", booking(["t_2"], "20:00", 2), token, "right"), 201)
    swap = {"moves": [{"reference": left["reference"], "table_ids": ["t_2"]},
                      {"reference": right["reference"], "table_id": "t_1"}]}
    swap_result = check(request(stage2, "POST", "/reservation-moves", swap, token, "swap"), 201)
    assert [item["table_ids"] for item in swap_result["reservations"]] == [["t_2"], ["t_1"]]
    assert check(request(stage2, "POST", "/reservation-moves", swap, token, "swap"), 200) == swap_result
    conflict = {"moves": [{"reference": left["reference"], "table_ids": ["t_2", "t_3"]},
                          {"reference": right["reference"], "table_ids": ["t_2", "t_1"]}]}
    check(request(stage2, "POST", "/reservation-moves", conflict, token, "conflict"), 409, "table_unavailable")
    assert check(request(stage2, "GET", "/reservations/" + left["reference"], token=token), 200) == swap_result["reservations"][0]
    assert check(request(stage2, "GET", "/reservations/" + right["reference"], token=token), 200) == swap_result["reservations"][1]
    exported = check(request(stage2, "GET", "/_test/export"), 200)
    check(request(stage2, "POST", "/_test/import", exported), 204)
    assert check(request(stage2, "GET", "/_test/export"), 200) == exported
    assert check(request(stage2, "POST", "/reservation-moves", swap, token, "swap"), 200) == swap_result

    seed_fixture = copy.deepcopy(FIXTURE)
    seed_fixture["reservations"] = [{"id": "seed_1", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_arden",
                                     "table_ids": ["t_1", "t_2"], "starts_at_local": "2035-01-01T18:00", "party_size": 4,
                                     "status": "cancelled"}]
    check(request(stage2, "POST", "/_test/reset", seed_fixture), 204)
    token = login(stage2)
    seeded = check(request(stage2, "GET", "/reservations/SEED01", token=token), 200)
    assert seeded["status"] == "cancelled" and seeded["table_ids"] == ["t_2", "t_1"]
    check(request(stage2, "POST", "/reservations", pair_body, token, "cancelled-seed-free"), 201)

    legacy = copy.deepcopy(FIXTURE)
    legacy["restaurants"][0].pop("combinable")
    check(request(stage1, "POST", "/_test/reset", legacy), 204)
    legacy_token = login(stage1)
    old_body = {"restaurant_id": "r_arden", "table_id": "t_1", "starts_at_local": "2035-01-01T18:00", "party_size": 2}
    old_response = check(request(stage1, "POST", "/reservations", old_body, legacy_token, "lost-before-upgrade"), 201)
    old_second_body = {**old_body, "table_id": "t_2"}
    old_second = check(request(stage1, "POST", "/reservations", old_second_body, legacy_token, "second-before-upgrade"), 201)
    old_swap_body = {"moves": [{"reference": old_response["reference"], "table_id": "t_2"},
                               {"reference": old_second["reference"], "table_id": "t_1"}]}
    old_swap = check(request(stage1, "POST", "/reservation-moves", old_swap_body, legacy_token, "swap-before-upgrade"), 201)
    assert "table_ids" not in old_response
    old_export = check(request(stage1, "GET", "/_test/export"), 200)
    check(request(stage2, "POST", "/_test/import", old_export), 204)
    upgraded = check(request(stage2, "GET", "/reservations/" + old_response["reference"], token=legacy_token), 200)
    assert upgraded["table_ids"] == ["t_2"] and upgraded["table_id"] == "t_2"
    replay = check(request(stage2, "POST", "/reservations", old_body, legacy_token, "lost-before-upgrade"), 200)
    assert replay == old_response and "table_ids" not in replay
    assert check(request(stage2, "POST", "/reservation-moves", old_swap_body, legacy_token, "swap-before-upgrade"), 200) == old_swap
    imported = check(request(stage2, "GET", "/_test/export"), 200)
    assert imported["state"]["tokens"] == old_export["state"]["tokens"]
    assert imported["state"]["receipts"] == old_export["state"]["receipts"]
    assert imported["state"]["users"] == old_export["state"]["users"]

    check(request(stage2, "POST", "/_test/reset", FIXTURE), 204)
    token = login(stage2)
    gate = threading.Barrier(20)
    def competing(index):
        gate.wait(timeout=10)
        ids = ["t_1", "t_2"] if index % 2 else ["t_2"]
        return request(stage2, "POST", "/reservations", booking(ids, size=2), token, f"race-{index}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        statuses = [result[0] for result in pool.map(competing, range(20))]
    assert sorted(statuses) == [201] + [409] * 19, statuses
    return {"checks": checks, "competing_requests": 20, "legacy_response_preserved": True,
            "combined_pair_order": True, "atomic_shared_member": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--stage1-url", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/"), args.stage1_url.rstrip("/")), sort_keys=True))
