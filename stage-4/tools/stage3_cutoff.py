"""Accepted-cutoff immutability and stale-revision precedence check."""
import argparse, copy, json
from datetime import datetime, timedelta, timezone
from stage3_acceptance import FIXTURE, call, expect, policy

def run(base):
    fixture=copy.deepcopy(FIXTURE); expect(call(base,"POST","/_test/reset",fixture),204)
    token=expect(call(base,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}),200)["token"]
    tomorrow=(datetime.now(timezone.utc).date()+timedelta(days=1)).isoformat()
    old=policy(tomorrow); old["cancellation_cutoff_minutes"]=10080
    expect(call(base,"POST","/restaurants/r_arden/policies",old,token,"old-cutoff"),201)
    body={"restaurant_id":"r_arden","table_id":"t_1","starts_at_local":tomorrow+"T18:00","party_size":2}
    booking=expect(call(base,"POST","/reservations",body,token,"cutoff-booking"),201)
    newer=policy(tomorrow); newer["cancellation_cutoff_minutes"]=0
    expect(call(base,"POST","/restaurants/r_arden/policies",newer,token,"new-cutoff"),201)
    expect(call(base,"PATCH","/reservations/"+booking["reference"],{"table_id":"t_2","expected_revision":99},token),409,"stale_revision")
    expect(call(base,"PATCH","/reservations/"+booking["reference"],{"table_id":"t_2","expected_revision":1},token),409,"cutoff_passed")
    current=expect(call(base,"GET","/reservations/"+booking["reference"],token=token),200)
    history=expect(call(base,"GET","/reservations/"+booking["reference"]+"/history",token=token),200)
    assert current==booking and len(history["entries"])==1
    return {"stale_before_cutoff":True,"accepted_cutoff_immutable":True,"rollback":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--base-url",required=True); args=parser.parse_args()
    print(json.dumps(run(args.base_url.rstrip("/")),sort_keys=True))
