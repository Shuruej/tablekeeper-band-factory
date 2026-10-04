"""Focused same-slot and expected-revision contention checks."""
import argparse, concurrent.futures, copy, json
from stage3_acceptance import FIXTURE, call, expect

def run(base):
    fixture=copy.deepcopy(FIXTURE); expect(call(base,"POST","/_test/reset",fixture),204)
    token=expect(call(base,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    body={"restaurant_id":"r_arden","table_id":"t_2","starts_at_local":"2035-03-01T18:00","party_size":2}
    def create(i): return call(base,"POST","/reservations",body,token,"race-"+str(i))[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool: statuses=list(pool.map(create,range(20)))
    assert statuses.count(201)==1 and statuses.count(409)==19, statuses
    booking=expect(call(base,"GET","/reservations",token=token),200)["reservations"][0]
    def amend(size): return call(base,"PATCH",f"/reservations/{booking['reference']}",{"party_size":size,"expected_revision":1},token)[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: revisions=list(pool.map(amend,(3,4)))
    assert revisions.count(200)==1 and revisions.count(409)==1, revisions
    return {"same_slot":{"201":1,"409":19},"cas":{"200":1,"409":1}}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
