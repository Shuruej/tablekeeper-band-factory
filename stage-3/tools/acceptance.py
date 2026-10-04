import argparse, json, sys, time, urllib.request, urllib.error, concurrent.futures, threading
from datetime import datetime

def call(base,method,path,body=None,headers=None):
    raw=None if body is None else json.dumps(body).encode(); h={"Content-Type":"application/json"}; h.update(headers or {})
    req=urllib.request.Request(base+path,data=raw,method=method,headers=h); started=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=10) as r: data=r.read(); return r.status,(json.loads(data) if data else None),time.perf_counter()-started
    except urllib.error.HTTPError as e:
        data=e.read(); return e.code,(json.loads(data) if data else None),time.perf_counter()-started

FIXTURE={"users":[{"id":"u_ada","email":"ada@example.com","password":"correct horse","display_name":"Ada"}],"restaurants":[{"id":"r_anker","name":"Zum Anker","timezone":"Europe/Berlin","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thu","opens":"18:00","closes":"23:00"}],"tables":[{"id":"t_1","label":"1","capacity":4},{"id":"t_2","label":"2","capacity":4}]}],"reservations":[]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--base-url",required=True); ap.add_argument("--destination-url"); a=ap.parse_args(); base=a.base_url.rstrip("/"); checks=0; maximum=0
    def expect(method,path,status,body=None,headers=None):
        nonlocal checks,maximum
        s,v,t=call(base,method,path,body,headers); maximum=max(maximum,t); checks+=1
        assert s==status,(method,path,s,v); return v
    expect("GET","/health",200); expect("POST","/_test/reset",204,FIXTURE)
    login=expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"}); auth={"Authorization":"Bearer "+login["token"]}
    expect("GET","/restaurants",200); expect("GET","/availability?restaurant_id=r_anker&date=2027-09-23&party_size=4",200)
    booking={"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2027-09-23T19:00","party_size":4}; h={**auth,"Idempotency-Key":"k1"}
    first=expect("POST","/reservations",201,booking,h); selfsame=expect("POST","/reservations",200,booking,h); assert first==selfsame
    expect("POST","/reservations",409,{**booking,"party_size":3},h); expect("GET","/reservations",200,headers=auth); expect("GET","/reservations/"+first["reference"],200,headers=auth)
    exported=expect("GET","/_test/export",200); source_auth=dict(auth)
    expect("POST","/reservations/"+first["reference"]+"/cancel",200,{},auth); expect("POST","/_test/import",204,exported); expect("POST","/reservations",200,booking,h)
    second=expect("POST","/reservations",201,{**booking,"table_id":"t_2"},{**auth,"Idempotency-Key":"k2"})
    swap={"moves":[{"reference":first["reference"],"table_id":"t_2"},{"reference":second["reference"],"table_id":"t_1"}]}
    swapped=expect("POST","/reservation-moves",201,swap,{**auth,"Idempotency-Key":"swap"})
    assert [r["table_id"] for r in swapped["reservations"]]==["t_2","t_1"]
    assert expect("POST","/reservation-moves",200,swap,{**auth,"Idempotency-Key":"swap"})==swapped
    batch_export=expect("GET","/_test/export",200)
    expect("POST","/reservation-moves",409,{"moves":[{"reference":first["reference"],"table_id":"t_1"}]},{**auth,"Idempotency-Key":"bad-move"})
    assert expect("GET","/reservations/"+first["reference"],200,headers=auth)["table_id"]=="t_2"
    unchanged={"moves":[{"reference":first["reference"]}]}
    no_op=expect("POST","/reservation-moves",201,unchanged,{**auth,"Idempotency-Key":"bad-move"})
    assert no_op["reservations"][0]==swapped["reservations"][0]
    collision={"moves":[{"reference":first["reference"]},{"reference":second["reference"],"table_id":"t_2"}]}
    response=expect("POST","/reservation-moves",409,collision,{**auth,"Idempotency-Key":"member-collision"})
    assert response["error"]["code"]=="table_unavailable"
    assert expect("GET","/reservations/"+second["reference"],200,headers=auth)["table_id"]=="t_1"
    typed={**booking,"starts_at_local":"2027-09-23T20:30","flag":True}
    expect("POST","/reservations",201,typed,{**auth,"Idempotency-Key":"typed"})
    response=expect("POST","/reservations",409,{**typed,"flag":1},{**auth,"Idempotency-Key":"typed"})
    assert response["error"]["code"]=="idempotency_key_reuse"
    numeric={**typed,"table_id":"t_2","flag":1}
    numeric_first=expect("POST","/reservations",201,numeric,{**auth,"Idempotency-Key":"numeric"})
    assert expect("POST","/reservations",200,{**numeric,"flag":1.0},{**auth,"Idempotency-Key":"numeric"})==numeric_first
    expect("POST","/_test/reset",204,FIXTURE)
    auth={"Authorization":"Bearer "+expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"})["token"]}
    def race(same_key):
        gate=threading.Barrier(50)
        def hit(i):
            gate.wait(timeout=10)
            return call(base,"POST","/reservations",booking,{**auth,"Idempotency-Key":"race" if same_key else f"race-{i}"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool: results=list(pool.map(hit,range(50)))
        counts={status:sum(r[0]==status for r in results) for status in {r[0] for r in results}}
        expected={201:1,200:49} if same_key else {201:1,409:49}
        assert counts==expected,counts
        if same_key: assert len({json.dumps(r[1],sort_keys=True) for r in results})==1
        else: assert all(r[1]["error"]["code"]=="table_unavailable" for r in results if r[0]==409)
        return max(r[2] for r in results)
    maximum=max(maximum,race(True)); expect("POST","/_test/reset",204,FIXTURE)
    auth={"Authorization":"Bearer "+expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"})["token"]}
    maximum=max(maximum,race(False))
    expect("POST","/_test/reset",204,FIXTURE)
    auth={"Authorization":"Bearer "+expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"})["token"]}
    left=expect("POST","/reservations",201,booking,{**auth,"Idempotency-Key":"left"})
    right=expect("POST","/reservations",201,{**booking,"table_id":"t_2"},{**auth,"Idempotency-Key":"right"})
    batch={"moves":[{"reference":left["reference"],"table_id":"t_2"},{"reference":right["reference"],"table_id":"t_1"}]}
    gate=threading.Barrier(50)
    def batch_hit(_):
        gate.wait(timeout=10)
        return call(base,"POST","/reservation-moves",batch,{**auth,"Idempotency-Key":"batch-race"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool: results=list(pool.map(batch_hit,range(50)))
    assert sorted(r[0] for r in results)==[200]*49+[201]
    assert len({json.dumps(r[1],sort_keys=True) for r in results})==1
    maximum=max(maximum,max(r[2] for r in results))
    assert expect("GET","/reservations/"+left["reference"],200,headers=auth)["table_id"]=="t_2"
    expect("POST","/_test/reset",204,FIXTURE)
    auth={"Authorization":"Bearer "+expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"})["token"]}
    original=expect("POST","/reservations",201,booking,{**auth,"Idempotency-Key":"mixed-original"})
    gate=threading.Barrier(50)
    def mixed_hit(i):
        gate.wait(timeout=10)
        if i%3==0: return call(base,"POST","/reservations",{**booking,"table_id":"t_2"},{**auth,"Idempotency-Key":f"mixed-{i}"})
        if i%3==1: return call(base,"PATCH","/reservations/"+original["reference"],{"table_id":"t_2"},auth)
        return call(base,"POST","/reservations/"+original["reference"]+"/cancel",None,auth)
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool: results=list(pool.map(mixed_hit,range(50)))
    assert all(r[0] in (200,201,409) for r in results),{r[0] for r in results}
    maximum=max(maximum,max(r[2] for r in results))
    listing=expect("GET","/reservations",200,headers=auth)["reservations"]
    confirmed=[r for r in listing if r["status"]=="confirmed"]
    for i,one in enumerate(confirmed):
        for two in confirmed[:i]:
            if one["table_id"]==two["table_id"]:
                assert datetime.fromisoformat(one["starts_at"])>=datetime.fromisoformat(two["ends_at"]) or datetime.fromisoformat(two["starts_at"])>=datetime.fromisoformat(one["ends_at"])
    if a.destination_url:
        dest=a.destination_url.rstrip("/")
        s,_,_=call(dest,"POST","/_test/reset",FIXTURE); assert s==204
        s,dest_user,_=call(dest,"POST","/auth/signup",{"email":"destination@example.com","password":"correct horse","display_name":"Destination"}); assert s==201
        dest_auth={"Authorization":"Bearer "+dest_user["token"]}
        s,_,_=call(dest,"POST","/_test/import",batch_export); assert s==204
        s,_,_=call(dest,"GET","/reservations",None,source_auth); assert s==200
        s,_,_=call(dest,"GET","/reservations",None,dest_auth); assert s==401
        s,replayed,_=call(dest,"POST","/reservations",booking,h); assert s==200 and replayed==first
        s,replayed,_=call(dest,"POST","/reservation-moves",swap,{**source_auth,"Idempotency-Key":"swap"}); assert s==200 and replayed==swapped
        s,_,_=call(dest,"POST","/auth/login",{"email":"ada@example.com","password":"correct horse"}); assert s==200
        invalid_export={**batch_export,"track":"wrong"}
        s,_,_=call(dest,"POST","/_test/import",invalid_export); assert s==422
        invalid_state=json.loads(json.dumps(batch_export))
        invalid_state["state"]["tokens"]["bad-token"]="missing-user"
        s,_,_=call(dest,"POST","/_test/import",invalid_state); assert s==422
        s,_,_=call(dest,"GET","/reservations",None,source_auth); assert s==200
        s,_,_=call(dest,"POST","/_test/import",batch_export); assert s==204
        s,listing,_=call(dest,"GET","/reservations",None,source_auth); assert s==200 and len(listing["reservations"])==2
    zoned={**FIXTURE,"restaurants":[{**FIXTURE["restaurants"][0],"id":"r_berlin","opening_hours":[{"weekday":d,"opens":"00:00","closes":"23:30"} for d in ("mon","tue","wed","thu","fri","sat","sun")]},{**FIXTURE["restaurants"][0],"id":"r_ny","timezone":"America/New_York","opening_hours":[{"weekday":d,"opens":"00:00","closes":"23:30"} for d in ("mon","tue","wed","thu","fri","sat","sun")]}]}
    expect("POST","/_test/reset",204,zoned)
    auth={"Authorization":"Bearer "+expect("POST","/auth/login",200,{"email":"ada@example.com","password":"correct horse"})["token"]}
    for rid,day,gap,fold,offset in (("r_berlin","2026-03-29","02:30",None,None),("r_ny","2026-03-08","02:30",None,None),("r_berlin","2026-10-25",None,"02:30","+02:00"),("r_ny","2026-11-01",None,"01:30","-04:00")):
        slots=expect("GET",f"/availability?restaurant_id={rid}&date={day}&party_size=4",200)["slots"]
        if gap:
            assert all(not s["starts_at_local"].endswith(gap) for s in slots)
            response=expect("POST","/reservations",422,{"restaurant_id":rid,"table_id":"t_1","starts_at_local":f"{day}T{gap}","party_size":4},{**auth,"Idempotency-Key":rid+day})
            assert response["error"]["code"]=="invalid_local_time"
        if fold:
            matching=[s for s in slots if s["starts_at_local"].endswith(fold)]
            assert len(matching)==1 and matching[0]["starts_at"].endswith(offset)
    berlin=expect("POST","/reservations",201,{"restaurant_id":"r_berlin","table_id":"t_1","starts_at_local":"2026-10-25T01:30","party_size":4},{**auth,"Idempotency-Key":"duration"})
    assert berlin["ends_at"].startswith("2026-10-25T02:00") and berlin["ends_at"].endswith("+01:00")
    print(json.dumps({"checks":checks,"raced_requests":200,"max_latency_seconds":round(maximum,4)}))

if __name__=="__main__":
    try: main()
    except Exception as e: print(f"FAIL: {e}",file=sys.stderr); raise
