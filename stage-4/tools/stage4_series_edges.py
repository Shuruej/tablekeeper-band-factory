"""Series-amend filtering, policy, DST, closure and rollback probes."""
import argparse, copy, json
from stage4_acceptance import fixture, login, book, closure
from stage3_acceptance import call, expect, policy

def run(base):
    # Cancelled and exception occurrences are filtered; an empty eligible set is a true no-op.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    anchor=book(base,token,"t_4",key="filter-anchor")
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":3,"interval_weeks":1},token,"filter-series"),201)
    first=series["occurrences"][1]["reservation"]; second=series["occurrences"][2]["reservation"]
    expect(call(base,"PATCH","/reservations/"+first["reference"],{"party_size":1,"expected_revision":1},token),200)
    expect(call(base,"POST","/reservations/"+second["reference"]+"/cancel",token=token),200)
    current=expect(call(base,"GET",f"/series/{series['series_id']}",token=token),200)
    assert current["revision"]==3 and current["occurrences"][1]["exception"] is True and current["occurrences"][2]["reservation"]["status"]=="cancelled"
    result=expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":3,"from_index":1,"local_time":"20:00"},token,"empty"),201)
    assert result==current

    # Each changed date adopts its applicable policy.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    anchor=book(base,token,"t_4",key="policy-anchor")
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"policy-series"),201)
    later=policy("2035-01-08",duration=120,capacities={"t_1":2,"t_2":4,"t_3":4,"t_4":4})
    expect(call(base,"POST","/restaurants/r_arden/policies",later,token,"later-policy"),201)
    amended=expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":1,"from_index":0,"local_time":"20:00"},token,"policy-amend"),201)
    assert amended["occurrences"][0]["reservation"]["accepted_terms"]["policy_version"]==0
    assert amended["occurrences"][1]["reservation"]["accepted_terms"]["policy_version"]==1

    # A closure applied when no booking overlaps blocks a later clock-time amendment atomically.
    close=closure("t_4",start="21:00",end="22:00")
    plan=expect(call(base,"POST","/restaurants/r_arden/replans",close,token,"amend-close"),201)
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{plan['plan_id']}/apply",{},token,"amend-close-apply"),201)
    before=expect(call(base,"GET","/_test/export"),200)
    expected=expect(call(base,"GET",f"/series/{series['series_id']}",token=token),200)["revision"]
    expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":expected,"from_index":0,"local_time":"21:00"},token,"closed-amend"),409,"table_unavailable")
    assert expect(call(base,"GET","/_test/export"),200)==before

    # A spring-forward gap is a non-occupancy error and rolls back every occurrence.
    gap=fixture(); rest=gap["restaurants"][0]; rest["timezone"]="America/New_York"
    rest["opening_hours"]=[{"weekday":d,"opens":"00:00","closes":"05:00"} for d in ("mon","tue","wed","thu","fri","sat","sun")]
    expect(call(base,"POST","/_test/reset",gap),204); token=login(base)
    anchor=book(base,token,"t_4",date="2035-03-04",time="01:30",key="gap-anchor")
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"gap-series"),201)
    before=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":1,"from_index":0,"local_time":"02:30"},token,"gap-amend"),422,"invalid_local_time")
    assert expect(call(base,"GET","/_test/export"),200)==before
    return {"filtering":True,"per_date_policy":True,"closure_rollback":True,"dst_gap_rollback":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
