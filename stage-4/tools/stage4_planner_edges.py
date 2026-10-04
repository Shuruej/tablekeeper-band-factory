"""Planner limits, half-open boundaries and conflict-source regressions."""
import argparse, copy, json
from stage4_acceptance import fixture, login, book, closure
from stage3_acceptance import call, expect

def run(base):
    # Half-open closure: a booking beginning at `to` is fixed and remains usable.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    affected=book(base,token,"t_2",time="18:00",size=4,key="affected")
    fixed=book(base,token,"t_3",time="18:30",size=4,key="fixed")
    edge=closure(start="18:00",end="18:30")
    plan=expect(call(base,"POST","/restaurants/r_arden/replans",edge,token,"edge"),201)
    assert [item["reference"] for item in plan["assignments"]]==[affected["reference"]]
    assert plan["assignments"][0]["table_ids"]==["t_4"]
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{plan['plan_id']}/apply",{},token,"edge-apply"),201)
    # The endpoint instant is free, while an overlapping write is blocked.
    book(base,token,"t_2",time="18:30",size=2,key="boundary-free")
    blocked={"restaurant_id":"r_arden","table_id":"t_2","starts_at_local":"2035-01-01T18:00","party_size":2}
    expect(call(base,"POST","/reservations",blocked,token,"boundary-blocked"),409,"table_unavailable")
    moves={"moves":[{"reference":affected["reference"],"table_id":"t_2","expected_revision":2}]}
    expect(call(base,"POST","/reservation-moves",moves,token,"closed-move"),409,"table_unavailable")
    anchor=book(base,token,"t_2",date="2034-12-25",time="18:00",key="series-anchor")
    before_series=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":2,"interval_weeks":1},token,"closed-series"),409,"table_unavailable")
    assert expect(call(base,"GET","/_test/export"),200)==before_series

    # A prior closure participates in a later preview's feasible option set.
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    prior=expect(call(base,"POST","/restaurants/r_arden/replans",closure("t_3"),token,"prior"),201)
    expect(call(base,"POST",f"/restaurants/r_arden/replans/{prior['plan_id']}/apply",{},token,"prior-apply"),201)
    needing_move=book(base,token,"t_2",size=4,key="prior-booking")
    later=expect(call(base,"POST","/restaurants/r_arden/replans",closure("t_2"),token,"later"),201)
    assignment=next(item for item in later["assignments"] if item["reference"]==needing_move["reference"])
    assert assignment["table_ids"]==["t_4"]

    # No feasible plan changes absolutely no state and creates no failed receipt.
    single=fixture(); rest=single["restaurants"][0]; rest["tables"]=[{"id":"t_1","label":"Only","capacity":2}]; rest["combinable"]=[]
    expect(call(base,"POST","/_test/reset",single),204); token=login(base); book(base,token,"t_1",key="only")
    before=expect(call(base,"GET","/_test/export"),200)
    expect(call(base,"POST","/restaurants/r_arden/replans",closure("t_1"),token,"impossible"),409,"no_feasible_plan")
    assert expect(call(base,"GET","/_test/export"),200)==before

    # Static and considered-booking planner bounds are enforced before search.
    too_many=fixture(); too_many["restaurants"][0]["tables"].extend([
        {"id":"t_5","label":"Five","capacity":2},{"id":"t_6","label":"Six","capacity":2},{"id":"t_7","label":"Seven","capacity":2}])
    expect(call(base,"POST","/_test/reset",too_many),204); token=login(base)
    expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"table-limit"),422,"planning_limit")
    many=fixture(); many["restaurants"][0]["opening_hours"]=[{"weekday":d,"opens":"00:00","closes":"23:59"} for d in ("mon","tue","wed","thu","fri","sat","sun")]
    expect(call(base,"POST","/_test/reset",many),204); token=login(base)
    for index,hour in enumerate(range(1,8)):
        book(base,token,"t_1",time=f"{hour:02d}:00",key=f"many-{index}")
    wide=closure("t_2",start="00:00",end="10:00")
    expect(call(base,"POST","/restaurants/r_arden/replans",wide,token,"booking-limit"),422,"planning_limit")
    return {"half_open":True,"fixed_booking":True,"no_feasible_rollback":True,"planning_limits":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
