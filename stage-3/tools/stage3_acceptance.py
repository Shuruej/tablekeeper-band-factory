"""Focused Stage 3 policy/history/series/upgrade acceptance checks."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request


HOURS = [{"weekday": day, "opens": "18:00", "closes": "23:00"}
         for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
FIXTURE = {
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}],
    "restaurants": [{"id": "r_arden", "name": "Arden", "timezone": "UTC", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
        "opening_hours": HOURS, "manager_user_ids": ["u_ada"],
        "tables": [{"id": "t_1", "label": "One", "capacity": 2},
                   {"id": "t_2", "label": "Two", "capacity": 4},
                   {"id": "t_3", "label": "Three", "capacity": 4}],
        "combinable": [["t_1", "t_2"]]}], "reservations": []}


def call(base, method, path, body=None, token=None, key=None):
    headers = {}
    if body is not None: headers["Content-Type"] = "application/json"
    if token: headers["Authorization"] = "Bearer " + token
    if key: headers["Idempotency-Key"] = key
    raw = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=raw, method=method, headers=headers)
    try: response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error: response = error
    with response:
        payload = response.read()
        return response.status, json.loads(payload) if payload else None


def expect(actual, status, code=None):
    assert actual[0] == status, (status, actual)
    if code: assert actual[1]["error"]["code"] == code, (code, actual)
    return actual[1]


def policy(effective, duration=60, capacities=None):
    return {"effective_from": effective, "slot_minutes": 30,
            "reservation_duration_minutes": duration, "cancellation_cutoff_minutes": 0,
            "opening_hours": HOURS, "capacities": capacities or {"t_1": 2, "t_2": 4, "t_3": 4}}


def run(base, stage2):
    checks = 0
    expect(call(base, "POST", "/_test/reset", FIXTURE), 204)
    ada = expect(call(base, "POST", "/auth/login", {"email":"ada@example.com","password":"correct horse"}), 200)["token"]
    bob = expect(call(base, "POST", "/auth/login", {"email":"bob@example.com","password":"correct horse"}), 200)["token"]
    p1 = expect(call(base,"POST","/restaurants/r_arden/policies",policy("2035-01-01",60),ada,"p1"),201)
    p2 = expect(call(base,"POST","/restaurants/r_arden/policies",policy("2035-01-01",90),ada,"p2"),201)
    assert (p1["policy_version"],p2["policy_version"]) == (1,2)
    assert expect(call(base,"POST","/restaurants/r_arden/policies",policy("2035-01-01",60),ada,"p1"),200)==p1
    assert len(expect(call(base,"GET","/restaurants/r_arden/policies"),200)["policies"])==2
    expect(call(base,"POST","/restaurants/r_arden/policies",policy("2035-01-01"),bob,"x"),403,"forbidden")
    bad=policy("2035-01-01"); bad["capacities"]={"t_1":2}
    expect(call(base,"POST","/restaurants/r_arden/policies",bad,ada,"bad"),422,"validation_failed"); checks+=5
    explained=expect(call(base,"GET","/availability?restaurant_id=r_arden&date=2035-01-01&party_size=4&explain=true"),200)
    slot=explained["slots"][0]
    assert [x["table_id"] for x in slot["explain"]]==["t_1","t_2","t_3"]
    assert [x["table_id"] for x in slot["explain"] if x["available"]]==slot["available_table_ids"]
    assert all([r["rule"] for r in x["rules"]]==["capacity","no_overlap"] and x["policy_version"]==2 for x in slot["explain"])
    expect(call(base,"GET","/availability?restaurant_id=r_arden&date=2035-01-01&party_size=4&explain=false"),422,"validation_failed"); checks+=4
    body={"restaurant_id":"r_arden","table_id":"t_2","starts_at_local":"2035-01-01T18:00","party_size":4}
    created=expect(call(base,"POST","/reservations",body,ada,"book"),201)
    assert created["revision"]==1 and created["accepted_terms"]["policy_version"]==2 and created["accepted_terms"]["reservation_duration_minutes"]==90
    history=expect(call(base,"GET",f"/reservations/{created['reference']}/history",token=ada),200)["entries"]
    assert [x["event"] for x in history]==["created"] and history[0]["revision"]==1
    expect(call(base,"GET",f"/reservations/{created['reference']}/history"),404,"not_found")
    expect(call(base,"GET",f"/reservations/{created['reference']}/decision",token=bob),404,"not_found"); checks+=5
    noop=expect(call(base,"PATCH",f"/reservations/{created['reference']}",{"table_id":"t_2","expected_revision":1},ada),200)
    assert noop==created
    changed=expect(call(base,"PATCH",f"/reservations/{created['reference']}",{"table_id":"t_3","expected_revision":1},ada),200)
    assert changed["revision"]==2
    expect(call(base,"PATCH",f"/reservations/{created['reference']}",{"party_size":3,"expected_revision":1},ada),409,"stale_revision")
    history=expect(call(base,"GET",f"/reservations/{created['reference']}/history",token=ada),200)["entries"]
    assert [x["event"] for x in history]==["created","changed"] and history[1]["changes"]==[{"field":"table_id","from":"t_2","to":"t_3"}]; checks+=4
    series=expect(call(base,"POST","/series",{"anchor_reference":created["reference"],"count":3,"interval_weeks":1},ada,"series"),201)
    assert series["revision"]==1 and [x["index"] for x in series["occurrences"]]==[0,1,2]
    assert series["occurrences"][0]["reservation"]["reservation_id"]==created["reservation_id"]
    second=series["occurrences"][1]["reservation"]
    expect(call(base,"PATCH",f"/reservations/{second['reference']}",{"party_size":3,"expected_revision":1},ada),200)
    current=expect(call(base,"GET",f"/series/{series['series_id']}",token=ada),200)
    assert current["revision"]==2 and current["occurrences"][1]["exception"] is True
    expect(call(base,"GET",f"/series/{series['series_id']}",token=bob),404,"not_found"); checks+=5
    other_body={"restaurant_id":"r_arden","table_id":"t_2","starts_at_local":"2035-01-08T18:00","party_size":4}
    other=expect(call(base,"POST","/reservations",other_body,ada,"other-anchor"),201)
    other_series=expect(call(base,"POST","/series",{"anchor_reference":other["reference"],"count":2,"interval_weeks":1},ada,"other-series"),201)
    left=series["occurrences"][2]["reservation"]; right=other_series["occurrences"][1]["reservation"]
    moves={"moves":[{"reference":left["reference"],"table_id":"t_2","expected_revision":1},
                     {"reference":right["reference"],"table_id":"t_3","expected_revision":1}]}
    expect(call(base,"POST","/reservation-moves",moves,ada,"swap"),201)
    first_after=expect(call(base,"GET",f"/series/{series['series_id']}",token=ada),200)
    other_after=expect(call(base,"GET",f"/series/{other_series['series_id']}",token=ada),200)
    assert first_after["revision"]==3 and first_after["occurrences"][2]["exception"] is True
    assert other_after["revision"]==2 and other_after["occurrences"][1]["exception"] is True
    frozen=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST","/_test/import",frozen),204)
    assert expect(call(base,"GET","/_test/export"),200)==frozen; checks+=5
    # A real accepted Stage 2 export imports; its original create receipt replays unchanged.
    legacy = dict(FIXTURE); legacy = json.loads(json.dumps(legacy)); legacy["restaurants"][0].pop("manager_user_ids")
    expect(call(stage2,"POST","/_test/reset",legacy),204)
    old_token=expect(call(stage2,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    old_body={"restaurant_id":"r_arden","table_id":"t_1","starts_at_local":"2035-02-01T18:00","party_size":2}
    old=expect(call(stage2,"POST","/reservations",old_body,old_token,"legacy-create"),201)
    export=expect(call(stage2,"GET","/_test/export"),200)
    expect(call(base,"POST","/_test/import",export),204)
    replay=expect(call(base,"POST","/reservations",old_body,old_token,"legacy-create"),200)
    assert replay==old and "revision" not in replay
    upgraded=expect(call(base,"GET",f"/reservations/{old['reference']}",token=old_token),200)
    assert upgraded["revision"]==1 and upgraded["accepted_terms"]["policy_version"]==0
    expect(call(base,"POST","/series",{"anchor_reference":old["reference"],"count":2,"interval_weeks":1},old_token,"legacy-series"),201); checks+=4
    return {"checks":checks,"policy_tie":True,"history_cas":True,"series":True,"stage2_upgrade_receipt":True}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); parser.add_argument("--stage2-url",required=True)
    args=parser.parse_args(); print(json.dumps(run(args.base_url.rstrip("/"),args.stage2_url.rstrip("/")),sort_keys=True))
