"""Real Stage 1-3 export upgrades with immutable legacy receipts."""
import argparse, copy, json
from stage3_acceptance import call, expect
from stage3_migration import upgrade
from stage4_acceptance import fixture

def stage3_upgrade(source,destination):
    value=fixture(); expect(call(source,"POST","/_test/reset",value),204)
    token=expect(call(source,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    body={"restaurant_id":"r_arden","table_id":"t_4","starts_at_local":"2035-01-01T18:00","party_size":2}
    original=expect(call(source,"POST","/reservations",body,token,"s3-create"),201)
    series=expect(call(source,"POST","/series",{"anchor_reference":original["reference"],"count":3,"interval_weeks":1},token,"s3-series"),201)
    moved=series["occurrences"][1]["reservation"]; cancelled=series["occurrences"][2]["reservation"]
    expect(call(source,"PATCH","/reservations/"+moved["reference"],{"party_size":1,"expected_revision":1},token),200)
    expect(call(source,"POST","/reservations/"+cancelled["reference"]+"/cancel",token=token),200)
    document=expect(call(source,"GET","/_test/export"),200)
    expect(call(destination,"POST","/_test/import",document),204)
    assert expect(call(destination,"POST","/reservations",body,token,"s3-create"),200)==original
    assert expect(call(destination,"POST","/series",{"anchor_reference":original["reference"],"count":3,"interval_weeks":1},token,"s3-series"),200)==series
    current=expect(call(destination,"GET",f"/series/{series['series_id']}",token=token),200)
    assert current["occurrences"][1]["exception"] is True and current["occurrences"][2]["reservation"]["status"]=="cancelled"
    amended=expect(call(destination,"POST",f"/series/{series['series_id']}/amend",{"expected_revision":current["revision"],"from_index":0,"local_time":"20:00"},token,"post-upgrade-amend"),201)
    assert amended["occurrences"][0]["reservation"]["starts_at_local"].endswith("T20:00")
    assert amended["occurrences"][1]["reservation"]["starts_at_local"]==current["occurrences"][1]["reservation"]["starts_at_local"]
    return {"old_create_receipt":True,"old_series_receipt":True,"exception_preserved":True,"cancelled_preserved":True,"stage4_amend":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--stage1-url",required=True); parser.add_argument("--stage2-url",required=True); parser.add_argument("--stage3-url",required=True); parser.add_argument("--destination",required=True)
    args=parser.parse_args(); destination=args.destination.rstrip("/")
    result={"stage1":upgrade(args.stage1_url.rstrip("/"),destination,"s1"),
            "stage2":upgrade(args.stage2_url.rstrip("/"),destination,"s2"),
            "stage3":stage3_upgrade(args.stage3_url.rstrip("/"),destination)}
    print(json.dumps(result,sort_keys=True))
