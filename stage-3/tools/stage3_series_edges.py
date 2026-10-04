"""Series DST, combined-table, policy-per-date and rollback probes."""
import argparse, copy, json
from stage3_acceptance import FIXTURE, HOURS, call, expect, policy

def fixture():
    value=copy.deepcopy(FIXTURE); rest=value["restaurants"][0]
    rest["timezone"]="America/New_York"; rest["opening_hours"]=[{"weekday":d,"opens":"00:00","closes":"05:00"} for d in ("mon","tue","wed","thu","fri","sat","sun")]
    return value

def run(base):
    gap=fixture(); expect(call(base,"POST","/_test/reset",gap),204)
    token=expect(call(base,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    body={"restaurant_id":"r_arden","table_ids":["t_2","t_1"],"starts_at_local":"2035-03-04T02:30","party_size":5}
    anchor=expect(call(base,"POST","/reservations",body,token,"gap-anchor"),201)
    before=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"gap-series"),422,"invalid_local_time")
    assert expect(call(base,"GET","/_test/export"),200)==before
    fold=fixture(); expect(call(base,"POST","/_test/reset",fold),204)
    token=expect(call(base,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    future=policy("2035-11-04",duration=120); future["opening_hours"]=fold["restaurants"][0]["opening_hours"]
    expect(call(base,"POST","/restaurants/r_arden/policies",future,token,"fold-policy"),201)
    body["starts_at_local"]="2035-10-28T01:30"
    anchor=expect(call(base,"POST","/reservations",body,token,"fold-anchor"),201)
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"fold-series"),201)
    generated=series["occurrences"][1]["reservation"]
    assert generated["starts_at"].endswith("-04:00")
    assert generated["accepted_terms"]["policy_version"]==1 and generated["accepted_terms"]["reservation_duration_minutes"]==120
    assert generated["table_ids"]==["t_1","t_2"] and generated["party_size"]==5
    return {"gap_atomic_rollback":True,"fold_first_occurrence":True,"per_date_policy":True,"combined_tables":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
