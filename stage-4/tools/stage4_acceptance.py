"""Focused Stage 4 planner/apply/closure/series-amend acceptance checks."""
import argparse, copy, json
from stage3_acceptance import FIXTURE as BASE, call, expect, policy


def fixture(two_restaurants=False):
    value=copy.deepcopy(BASE); rest=value["restaurants"][0]
    rest["tables"]=[{"id":"t_1","label":"One","capacity":2},{"id":"t_2","label":"Two","capacity":4},
                    {"id":"t_3","label":"Three","capacity":4},{"id":"t_4","label":"Four","capacity":4}]
    rest["combinable"]=[["t_1","t_2"],["t_2","t_3"]]
    if two_restaurants:
        other=copy.deepcopy(rest); other.update(id="r_other",name="Other",manager_user_ids=["u_ada"])
        value["restaurants"].append(other)
    return value


def login(base,email="ada@example.com"):
    return expect(call(base,"POST","/auth/login",{"email":email,"password":"correct horse"}),200)["token"]


def book(base,token,table,date="2035-01-01",time="18:00",size=2,key="book",restaurant="r_arden"):
    body={"restaurant_id":restaurant,"table_id":table,"starts_at_local":f"{date}T{time}","party_size":size}
    return expect(call(base,"POST","/reservations",body,token,key),201)


def closure(table="t_2",date="2035-01-01",start="17:00",end="20:00"):
    return {"table_id":table,"from":f"{date}T{start}:00+00:00","to":f"{date}T{end}:00+00:00"}


def run(base,stage3):
    checks=0
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    # Objective: keep an unchanged assignment before saving seats; then minimize unused seats and rank.
    stay=book(base,token,"t_3",size=2,key="stay")
    move=book(base,token,"t_2",time="19:00",size=4,key="move")
    before=expect(call(base,"GET","/_test/export"),200)
    plan=expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"preview"),201)
    by_ref={item["reference"]:item for item in plan["assignments"]}
    assert by_ref[stay["reference"]]["table_ids"]==["t_3"] and by_ref[stay["reference"]]["changed"] is False
    assert by_ref[move["reference"]]["table_ids"]==["t_3"] and by_ref[move["reference"]]["changed"] is True
    assert plan["moved_count"]==1 and plan["unused_seats"]==2
    after=expect(call(base,"GET","/_test/export"),200)
    assert before["state"]["reservations"]==after["state"]["reservations"] and before["state"]["histories"]==after["state"]["histories"]
    assert before["state"]["closures"]==after["state"]["closures"] and before["state"]["restaurant_revisions"]==after["state"]["restaurant_revisions"]
    assert expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"preview"),200)==plan; checks+=7
    applied=expect(call(base,"POST",f"/restaurants/r_arden/replans/{plan['plan_id']}/apply",{},token,"apply"),201)
    current={item["reference"]:item for item in applied["reservations"]}
    assert current[stay["reference"]]["revision"]==stay["revision"]
    assert current[move["reference"]]["revision"]==move["revision"]+1
    history=expect(call(base,"GET",f"/reservations/{move['reference']}/history",token=token),200)["entries"]
    assert history[-1]["event"]=="reassigned" and history[-1]["plan_id"]==plan["plan_id"] and history[-1]["changes"]==[{"field":"table_ids","from":["t_2"],"to":["t_3"]}]
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{plan['plan_id']}/apply",{},token,"other-apply"),409,"plan_already_applied")
    assert expect(call(base,"POST",f"/restaurants/r_arden/replans/{plan['plan_id']}/apply",{},token,"apply"),200)==applied; checks+=5
    frozen=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST","/_test/import",frozen),204)
    assert expect(call(base,"GET","/_test/export"),200)==frozen; checks+=1
    slot=expect(call(base,"GET","/availability?restaurant_id=r_arden&date=2035-01-01&party_size=2&explain=true"),200)["slots"][0]
    t2=next(item for item in slot["explain"] if item["table_id"]=="t_2")
    assert t2["rules"][1]=={"rule":"no_overlap","holds":False} and "t_2" not in slot["available_table_ids"]
    assert all("t_2" not in option["table_ids"] for option in slot["available_options"])
    body={"restaurant_id":"r_arden","table_id":"t_2","starts_at_local":"2035-01-01T18:30","party_size":2}
    expect(call(base,"POST","/reservations",body,token,"closed-create"),409,"table_unavailable")
    expect(call(base,"PATCH",f"/reservations/{stay['reference']}",{"table_id":"t_2"},token),409,"table_unavailable"); checks+=5
    # Same-restaurant revision stales, while another restaurant does not.
    expect(call(base,"POST","/_test/reset",fixture(True)),204); token=login(base)
    fresh=expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"fresh"),201)
    book(base,token,"t_1",time="21:00",key="other-rest",restaurant="r_other")
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{fresh['plan_id']}/apply",{},token,"fresh-apply"),201)
    stale=expect(call(base,"POST","/restaurants/r_arden/replans",closure("t_1",start="20:00",end="22:00"),token,"stale"),201)
    book(base,token,"t_4",time="22:00",key="revision-change")
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{stale['plan_id']}/apply",{},token,"stale-apply"),409,"stale_plan"); checks+=3
    # Accepted-term capacity, not a later policy, controls a repair assignment.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    old=book(base,token,"t_2",size=4,key="old-capacity")
    changed=policy("2035-01-01",capacities={"t_1":2,"t_2":4,"t_3":2,"t_4":4})
    expect(call(base,"POST","/restaurants/r_arden/policies",changed,token,"lower-capacity"),201)
    cap_plan=expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"capacity-plan"),201)
    assignment=next(item for item in cap_plan["assignments"] if item["reference"]==old["reference"])
    assert assignment["table_ids"]==["t_3"], assignment; checks+=2
    # One plan moving multiple members increments the owning series exactly once.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    anchor=book(base,token,"t_2",size=4,key="repair-anchor")
    repaired_series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"repair-series"),201)
    broad=closure("t_2",start="17:00",end="20:00"); broad["to"]="2035-01-08T20:00:00+00:00"
    repair_plan=expect(call(base,"POST","/restaurants/r_arden/replans",broad,token,"repair-plan"),201)
    assert repair_plan["moved_count"]==2
    repair=expect(call(base,"POST",f"/restaurants/r_arden/replans/{repair_plan['plan_id']}/apply",{},token,"repair-apply"),201)
    repaired=expect(call(base,"GET",f"/series/{repaired_series['series_id']}",token=token),200)
    assert repaired["revision"]==2 and repair["restaurant_revision"]==3
    assert all(item["reservation"]["revision"]==2 and item["exception"] is False and item["reservation"]["accepted_terms"]==repaired_series["occurrences"][item["index"]]["reservation"]["accepted_terms"] for item in repaired["occurrences"]); checks+=4
    # Series amend changes eligible occurrences once and adopts current policy without exceptions.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    anchor=book(base,token,"t_4",key="anchor")
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":3,"interval_weeks":1},token,"series"),201)
    amended=expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":1,"from_index":1,"local_time":"20:00"},token,"amend"),201)
    assert amended["revision"]==2 and amended["occurrences"][0]["reservation"]==anchor
    assert all(item["reservation"]["starts_at_local"].endswith("T20:00") and item["reservation"]["revision"]==2 and item["exception"] is False for item in amended["occurrences"][1:])
    expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":1,"from_index":0,"local_time":"21:00"},token,"stale-amend"),409,"stale_revision")
    noop=expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":2,"from_index":1,"local_time":"20:00"},token,"noop"),201)
    assert noop["revision"]==2
    assert expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":1,"from_index":1,"local_time":"20:00"},token,"amend"),200)==amended; checks+=6
    return {"checks":checks,"planner_objective":True,"preview_apply":True,"closures":True,"series_amend":True}


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); parser.add_argument("--stage3-url",required=True)
    args=parser.parse_args(); print(json.dumps(run(args.base_url.rstrip("/"),args.stage3_url.rstrip("/")),sort_keys=True))
