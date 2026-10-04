"""Competing plan applications and series-amend CAS checks."""
import argparse, concurrent.futures, json
from stage4_acceptance import fixture, login, book, closure
from stage3_acceptance import call, expect

def run(base):
    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    booking=book(base,token,"t_2",size=4,key="race-booking")
    plan=expect(call(base,"POST","/restaurants/r_arden/replans",closure(),token,"race-plan"),201)
    path=f"/restaurants/r_arden/replans/{plan['plan_id']}/apply"
    def apply(index): return call(base,"POST",path,{},token,"apply-"+str(index))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(apply,range(2)))
    statuses=[item[0] for item in results]
    assert statuses.count(201)==1 and statuses.count(409)==1 and next(item for item in results if item[0]==409)[1]["error"]["code"]=="plan_already_applied", results
    current=expect(call(base,"GET","/reservations/"+booking["reference"],token=token),200)
    history=expect(call(base,"GET","/reservations/"+booking["reference"]+"/history",token=token),200)["entries"]
    export=expect(call(base,"GET","/_test/export"),200)["state"]
    assert current["revision"]==2 and [item["event"] for item in history]==["created","reassigned"] and len(export["closures"]["r_arden"])==1

    expect(call(base,"POST","/_test/reset",fixture()),204); token=login(base)
    anchor=book(base,token,"t_4",key="cas-anchor")
    series=expect(call(base,"POST","/series",{"anchor_reference":anchor["reference"],"count":3,"interval_weeks":1},token,"cas-series"),201)
    path=f"/series/{series['series_id']}/amend"
    def amend(time): return call(base,"POST",path,{"expected_revision":1,"from_index":0,"local_time":time},token,"amend-"+time)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: amendments=list(pool.map(amend,("20:00","21:00")))
    statuses=[item[0] for item in amendments]
    assert statuses.count(201)==1 and statuses.count(409)==1 and next(item for item in amendments if item[0]==409)[1]["error"]["code"]=="stale_revision", amendments
    current=expect(call(base,"GET",f"/series/{series['series_id']}",token=token),200)
    assert current["revision"]==2 and all(item["reservation"]["revision"]==2 for item in current["occurrences"])
    return {"apply":{"201":1,"409_plan_already_applied":1},"series_cas":{"201":1,"409_stale_revision":1}}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
