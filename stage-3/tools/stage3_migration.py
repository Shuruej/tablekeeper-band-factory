"""Real Stage 1/2 export upgrade and immutable receipt checks."""
import argparse, copy, json
from stage3_acceptance import FIXTURE, call, expect

def upgrade(source, destination, suffix):
    fixture=copy.deepcopy(FIXTURE)
    fixture["restaurants"][0].pop("manager_user_ids",None)
    fixture["restaurants"][0].pop("combinable",None)
    expect(call(source,"POST","/_test/reset",fixture),204)
    token=expect(call(source,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    body={"restaurant_id":"r_arden","table_id":"t_1","starts_at_local":"2035-04-01T18:00","party_size":2}
    original=expect(call(source,"POST","/reservations",body,token,"old-"+suffix),201)
    document=expect(call(source,"GET","/_test/export"),200)
    expect(call(destination,"POST","/_test/import",document),204)
    assert expect(call(destination,"POST","/reservations",body,token,"old-"+suffix),200)==original
    current=expect(call(destination,"GET","/reservations/"+original["reference"],token=token),200)
    assert current["revision"]==1 and current["accepted_terms"]["policy_version"]==0
    series=expect(call(destination,"POST","/series",{"anchor_reference":original["reference"],"count":2,"interval_weeks":1},token,"series-"+suffix),201)
    assert series["occurrences"][0]["reference"]==original["reference"]
    return {"receipt_exact":True,"normalized_revision":1,"series_adoption":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--stage1-url",required=True); parser.add_argument("--stage2-url",required=True); parser.add_argument("--destination",required=True)
    args=parser.parse_args()
    result={"stage1":upgrade(args.stage1_url.rstrip("/"),args.destination.rstrip("/"),"s1"),
            "stage2":upgrade(args.stage2_url.rstrip("/"),args.destination.rstrip("/"),"s2")}
    print(json.dumps(result,sort_keys=True))
