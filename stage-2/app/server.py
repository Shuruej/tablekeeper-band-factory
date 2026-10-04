import json, os, re, secrets, threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from .state import STORE, empty_state, hash_password, verify_password
from .seating import canonical_ids, input_ids, member_ids, seating_fields
from .validation import ApiError, EMAIL_RE, field_str, idempotency_key, invalid, malformed, party_size, require_object, json_clone
from .time_rules import UTC, enumerate_slots, iso, overlaps, parse_date, validate_slot, zone

HASH_LIMIT = threading.BoundedSemaphore(8)
STATIC = Path(__file__).with_name("static")
PAGES = {"/", "/signup", "/login", "/lookup"}
ASSETS = {"/assets/app.css": ("app.css", "text/css; charset=utf-8"),
          "/assets/app.js": ("app.js", "text/javascript; charset=utf-8")}

def public_res(r):
    result = {k: r[k] for k in ("reservation_id","reference","restaurant_id","party_size","status","starts_at_local","starts_at","ends_at","created_at")}
    result.update(seating_fields(member_ids(r)))
    return result

def by_reference(data, ref):
    return next((r for r in data["reservations"].values() if r["reference"] == ref), None)

def authenticate(headers, data):
    value = headers.get("Authorization", "")
    if not value.startswith("Bearer ") or not value[7:] or value[7:] not in data["tokens"]: raise ApiError(401, "unauthenticated")
    return data["tokens"][value[7:]]

def table_for(rest, table_id):
    return next((t for t in rest["tables"] if t["id"] == table_id), None)

def build_candidate(data, existing, restaurant_id, table_ids, starts_local, size):
    if isinstance(restaurant_id,str) and len(restaurant_id)>64: invalid("invalid restaurant_id")
    rest = data["restaurants"].get(restaurant_id)
    if rest is None: raise ApiError(404, "not_found")
    ids, capacity = canonical_ids(rest, table_ids)
    size = party_size(size)
    start, end = validate_slot(rest, starts_local)
    if size > capacity: raise ApiError(422, "party_exceeds_capacity")
    result = dict(existing or {})
    result.pop("table_id", None)
    result.update(restaurant_id=restaurant_id, **seating_fields(ids), party_size=size, starts_at_local=starts_local, starts_at=iso(start), ends_at=iso(end))
    return result

def has_conflict(data, candidate, excluded_ids=()):
    a, b = datetime.fromisoformat(candidate["starts_at"]), datetime.fromisoformat(candidate["ends_at"])
    for rid, other in data["reservations"].items():
        if rid in excluded_ids or other["status"] != "confirmed": continue
        if other["restaurant_id"] == candidate["restaurant_id"] and set(member_ids(other)) & set(member_ids(candidate)) and overlaps(a,b,datetime.fromisoformat(other["starts_at"]),datetime.fromisoformat(other["ends_at"])): return True
    return False

def check_cutoff(data, res):
    rest = data["restaurants"][res["restaurant_id"]]
    start = datetime.fromisoformat(res["starts_at"]).astimezone(UTC)
    if datetime.now(UTC) >= start - timedelta(minutes=rest["cancellation_cutoff_minutes"]): raise ApiError(409, "cutoff_passed")

def fixture_type(obj, key, expected):
    if key not in obj: invalid(f"missing {key}")
    value = obj[key]
    if type(value) is not expected: malformed(f"{key} has wrong JSON type")
    return value

def validate_fixture(fixture):
    require_object(fixture)
    users = fixture_type(fixture,"users",list)
    restaurants = fixture_type(fixture,"restaurants",list)
    reservations = fixture_type(fixture,"reservations",list)
    data = empty_state()
    for u in users:
        if type(u) is not dict: malformed("user has wrong JSON type")
        uid, email, password, display = (fixture_type(u,k,str) for k in ("id","email","password","display_name"))
        if not uid or len(uid)>64 or not EMAIL_RE.fullmatch(email) or len(password)<8 or uid in data["users"] or email in data["emails"]: invalid("invalid user")
        with HASH_LIMIT: salt,digest=hash_password(password)
        data["users"][uid]={"id":uid,"email":email,"display_name":display,"salt":salt,"password_hash":digest}; data["emails"][email]=uid
    for rest in restaurants:
        if type(rest) is not dict: malformed("restaurant has wrong JSON type")
        needed=("id","name","timezone","slot_minutes","reservation_duration_minutes","cancellation_cutoff_minutes","opening_hours","tables")
        if any(k not in rest for k in needed): invalid("invalid restaurant")
        rid=fixture_type(rest,"id",str)
        fixture_type(rest,"name",str); fixture_type(rest,"timezone",str)
        if not rid or len(rid)>64 or rid in data["restaurants"]: invalid("invalid restaurant")
        zone(rest["timezone"])
        for k in ("slot_minutes","reservation_duration_minutes"):
            if fixture_type(rest,k,int) < 1: invalid("invalid restaurant")
        if fixture_type(rest,"cancellation_cutoff_minutes",int) < 0: invalid("invalid restaurant")
        hours=fixture_type(rest,"opening_hours",list); tables=fixture_type(rest,"tables",list)
        weekdays=set()
        for h in hours:
            if type(h) is not dict: malformed("hour has wrong JSON type")
            weekday=fixture_type(h,"weekday",str)
            opens=fixture_type(h,"opens",str); closes=fixture_type(h,"closes",str)
            if weekday not in ("mon","tue","wed","thu","fri","sat","sun") or weekday in weekdays: invalid("invalid hours")
            from .time_rules import parse_hhmm
            if parse_hhmm(opens) >= parse_hhmm(closes): invalid("invalid hours")
            weekdays.add(weekday)
        tids=set()
        for t in tables:
            if type(t) is not dict: malformed("table has wrong JSON type")
            tid=fixture_type(t,"id",str); fixture_type(t,"label",str); capacity=fixture_type(t,"capacity",int)
            if not tid or len(tid)>64 or tid in tids or capacity<1: invalid("invalid table")
            tids.add(t["id"])
        combinations=rest.get("combinable",[])
        if type(combinations) is not list: malformed("combinable has wrong JSON type")
        seen_pairs=set()
        for pair in combinations:
            if type(pair) is not list or any(type(value) is not str for value in pair): malformed("combinable entry has wrong JSON type")
            if len(pair)!=2 or pair[0]==pair[1] or any(value not in tids for value in pair) or frozenset(pair) in seen_pairs: invalid("invalid combinable pair")
            seen_pairs.add(frozenset(pair))
        saved=json_clone(rest); saved["combinable"]=json_clone(combinations)
        data["restaurants"][rid]=saved; data["restaurant_order"].append(rid)
    refs=set()
    for seeded in reservations:
        if type(seeded) is not dict: malformed("reservation has wrong JSON type")
        if "party_size" not in seeded: invalid("missing party_size")
        party_size(seeded["party_size"])
        rid,ref,uid=(fixture_type(seeded,k,str) for k in ("id","reference","user_id"))
        for k in ("restaurant_id","starts_at_local"): fixture_type(seeded,k,str)
        ids=input_ids(seeded)
        status=seeded.get("status","confirmed")
        if type(status) is not str: malformed("status has wrong JSON type")
        if status not in ("confirmed","cancelled"): invalid("invalid reservation status")
        if not all((rid,ref,uid)) or len(rid)>64 or uid not in data["users"] or rid in data["reservations"] or ref in refs or not re.fullmatch(r"[A-Z0-9]{6,12}",ref): invalid("invalid reservation")
        candidate=build_candidate(data,None,seeded["restaurant_id"],ids,seeded["starts_at_local"],seeded["party_size"])
        candidate.update(reservation_id=rid,reference=ref,user_id=uid,status=status,created_at=datetime.now(UTC).isoformat(timespec="seconds"))
        if status=="confirmed" and has_conflict(data,candidate): invalid("overlapping seed")
        data["reservations"][rid]=candidate; data["reservation_order"].append(rid); refs.add(ref)
    while f"u_{data['next_user']}" in data["users"]: data["next_user"]+=1
    while f"res_{data['next_reservation']}" in data["reservations"]: data["next_reservation"]+=1
    return data

class Handler(BaseHTTPRequestHandler):
    server_version="Tablekeeper/1"
    def log_message(self, fmt, *args): pass
    def send_error(self, code, message=None, explain=None):
        status = 405 if code == 501 else 400 if code >= 500 else code
        self.send_json(status,{"error":{"code":"method_not_allowed" if code == 501 else "malformed_request","message":"method not allowed" if code == 501 else "malformed request"}})
    def send_json(self,status,value=None):
        raw=b"" if value is None else json.dumps(value,ensure_ascii=False,separators=(",",":")).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers()
        if raw: self.wfile.write(raw)
    def send_bytes(self,status,raw,content_type):
        self.send_response(status); self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(raw))); self.end_headers()
        self.wfile.write(raw)
    def body(self):
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<0 or length>10_000_000: malformed()
            raw=self.rfile.read(length)
            return json.loads(raw.decode("utf-8"),parse_constant=lambda _: malformed())
        except Exception: malformed()
    def dispatch(self, method):
        split=urlsplit(self.path); path=split.path
        if method=="GET" and path=="/health": return 200,{"status":"ok"}
        if method=="POST" and path=="/_test/reset":
            fixture=require_object(self.body())
            try: replacement=validate_fixture(fixture)
            except ApiError: raise
            except Exception: invalid("invalid fixture")
            STORE.reset(replacement); return 204,None
        if method=="GET" and path=="/_test/export": return 200,STORE.export()
        if method=="POST" and path=="/_test/import":
            document=require_object(self.body())
            try: STORE.import_document(document)
            except ApiError: raise
            except Exception: invalid("invalid state")
            return 204,None
        if method=="POST" and path in ("/auth/signup","/auth/login"): return self.auth(path,require_object(self.body()))
        if method=="GET" and path=="/restaurants":
            with STORE.lock: return 200,{"restaurants":[{k:STORE.data["restaurants"][rid][k] for k in ("id","name","timezone")} for rid in STORE.data["restaurant_order"]]}
        if method=="GET" and path.startswith("/restaurants/"):
            rid=unquote(path[len("/restaurants/"):])
            if len(rid)>64: invalid("invalid restaurant id")
            with STORE.lock:
                rest=STORE.data["restaurants"].get(rid)
                if rest is None: raise ApiError(404,"not_found")
                return 200,json_clone(rest)
        if method=="GET" and path=="/availability": return self.availability(parse_qs(split.query,keep_blank_values=True))
        body=None
        if method=="PATCH" or (method=="POST" and path in ("/reservations","/reservation-moves")):
            with STORE.lock: authenticate(self.headers,STORE.data)
            body=require_object(self.body())
        with STORE.lock:
            uid=authenticate(self.headers,STORE.data)
            if method=="POST" and path=="/reservations": return self.create(uid,body,path)
            if method=="POST" and path=="/reservation-moves": return self.moves(uid,body,path)
            if method=="GET" and path=="/reservations":
                vals=[public_res(r) for r in STORE.data["reservations"].values() if r["user_id"]==uid]
                vals.sort(key=lambda r:datetime.fromisoformat(r["starts_at"]).astimezone(UTC),reverse=True); return 200,{"reservations":vals}
            m=re.fullmatch(r"/reservations/([^/]+)(/cancel)?",path)
            if m:
                ref=unquote(m.group(1)); res=by_reference(STORE.data,ref)
                if res is None or res["user_id"]!=uid: raise ApiError(404,"not_found")
                if method=="GET" and not m.group(2): return 200,public_res(res)
                if method=="POST" and m.group(2):
                    if res["status"]=="cancelled": return 200,public_res(res)
                    check_cutoff(STORE.data,res); res["status"]="cancelled"; return 200,public_res(res)
                if method=="PATCH" and not m.group(2): return self.patch(res,body)
        raise ApiError(404,"not_found")
    def auth(self,path,body):
        email=field_str(body,"email"); password=field_str(body,"password")
        if path=="/auth/signup":
            display=field_str(body,"display_name")
            if not EMAIL_RE.fullmatch(email) or len(password)<8: invalid()
            with HASH_LIMIT: salt,digest=hash_password(password)
            with STORE.lock:
                if email in STORE.data["emails"]: raise ApiError(409,"email_taken")
                while f"u_{STORE.data['next_user']}" in STORE.data["users"]: STORE.data["next_user"]+=1
                uid=f"u_{STORE.data['next_user']}"; STORE.data["next_user"]+=1; token=secrets.token_urlsafe(32)
                STORE.data["users"][uid]={"id":uid,"email":email,"display_name":display,"salt":salt,"password_hash":digest}; STORE.data["emails"][email]=uid; STORE.data["tokens"][token]=uid
                return 201,{"user_id":uid,"display_name":display,"token":token}
        with STORE.lock:
            uid=STORE.data["emails"].get(email); user=json_clone(STORE.data["users"].get(uid)) if uid else None; generation=STORE.generation
        if user is None: raise ApiError(401,"unauthenticated")
        with HASH_LIMIT: valid=verify_password(password,user["salt"],user["password_hash"])
        if not valid: raise ApiError(401,"unauthenticated")
        with STORE.lock:
            if STORE.generation!=generation or STORE.data["users"].get(uid,{}).get("password_hash")!=user["password_hash"]: raise ApiError(401,"unauthenticated")
            token=secrets.token_urlsafe(32); STORE.data["tokens"][token]=uid; return 200,{"user_id":uid,"display_name":user["display_name"],"token":token}
    def availability(self,q):
        for k in ("restaurant_id","date","party_size"):
            if k not in q or len(q[k])!=1 or q[k][0]=="": invalid(f"missing {k}")
        if not re.fullmatch(r"[0-9]+",q["party_size"][0]): invalid("invalid party_size")
        if len(q["party_size"][0])>100: invalid("invalid party_size")
        if len(q["restaurant_id"][0])>64: invalid("invalid restaurant_id")
        size=int(q["party_size"][0]); party_size(size); day=parse_date(q["date"][0])
        with STORE.lock:
            rest=STORE.data["restaurants"].get(q["restaurant_id"][0])
            if rest is None: raise ApiError(404,"not_found")
            slots=[]
            for start,end in enumerate_slots(rest,day):
                avail=[]; options=[]
                for table in rest["tables"]:
                    c={"restaurant_id":rest["id"],"table_ids":[table["id"]],"starts_at":iso(start),"ends_at":iso(end)}
                    if table["capacity"]>=size and not has_conflict(STORE.data,c):
                        avail.append(table["id"]); options.append({"table_ids":[table["id"]],"capacity":table["capacity"]})
                for pair in rest.get("combinable",[]):
                    capacity=sum(table_for(rest,table_id)["capacity"] for table_id in pair)
                    c={"restaurant_id":rest["id"],"table_ids":pair,"starts_at":iso(start),"ends_at":iso(end)}
                    if capacity>=size and not has_conflict(STORE.data,c): options.append({"table_ids":list(pair),"capacity":capacity})
                slots.append({"starts_at_local":start.strftime("%Y-%m-%dT%H:%M"),"starts_at":iso(start),"available_table_ids":avail,"available_options":options})
            return 200,{"restaurant_id":rest["id"],"date":day.isoformat(),"timezone":rest["timezone"],"slots":slots}
    def create(self,uid,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        field_str(body,"restaurant_id",max_len=64)
        ids=input_ids(body)
        field_str(body,"starts_at_local")
        if "party_size" not in body: invalid("missing party_size")
        c=build_candidate(STORE.data,None,body["restaurant_id"],ids,body["starts_at_local"],body["party_size"])
        if has_conflict(STORE.data,c): raise ApiError(409,"table_unavailable")
        while f"res_{STORE.data['next_reservation']}" in STORE.data["reservations"]: STORE.data["next_reservation"]+=1
        rid=f"res_{STORE.data['next_reservation']}"; STORE.data["next_reservation"]+=1
        refs={x["reference"] for x in STORE.data["reservations"].values()}
        while True:
            ref=''.join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(8))
            if ref not in refs: break
        c.update(reservation_id=rid,reference=ref,user_id=uid,status="confirmed",created_at=datetime.now(UTC).isoformat(timespec="seconds"))
        STORE.data["reservations"][rid]=c; STORE.data["reservation_order"].append(rid); response=public_res(c); STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response
    def patch(self,res,body):
        if res["status"]=="cancelled": raise ApiError(409,"reservation_cancelled")
        check_cutoff(STORE.data,res)
        ids=input_ids(body,res)
        if "starts_at_local" in body and not isinstance(body["starts_at_local"],str): malformed("starts_at_local must be string")
        c=build_candidate(STORE.data,res,res["restaurant_id"],ids,body.get("starts_at_local",res["starts_at_local"]),body.get("party_size",res["party_size"]))
        if has_conflict(STORE.data,c,{res["reservation_id"]}): raise ApiError(409,"table_unavailable")
        res.clear(); res.update(c); return 200,public_res(res)
    def moves(self,uid,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        moves=body.get("moves")
        if not isinstance(moves,list) or not 1<=len(moves)<=8 or any(not isinstance(x,dict) for x in moves): invalid("invalid moves")
        refs=[]
        for m in moves:
            ref=m.get("reference")
            if not isinstance(ref,str) or not re.fullmatch(r"[A-Z0-9]{6,12}",ref) or ref in refs: invalid("invalid references")
            refs.append(ref)
        originals=[]; candidates=[]; restaurant_id=None
        for ref,m in zip(refs,moves):
            old=by_reference(STORE.data,ref)
            if old is None or old["user_id"]!=uid: raise ApiError(404,"not_found")
            if restaurant_id is None: restaurant_id=old["restaurant_id"]
            elif old["restaurant_id"]!=restaurant_id: invalid("different restaurants")
            originals.append(old)
            if old["status"]=="cancelled": raise ApiError(409,"reservation_cancelled")
            check_cutoff(STORE.data,old)
            ids=input_ids(m,old)
            if "starts_at_local" in m and not isinstance(m["starts_at_local"],str): malformed("starts_at_local must be string")
            candidates.append(build_candidate(STORE.data,old,old["restaurant_id"],ids,m.get("starts_at_local",old["starts_at_local"]),m.get("party_size",old["party_size"])))
        excluded={r["reservation_id"] for r in originals}
        for i,c in enumerate(candidates):
            if has_conflict(STORE.data,c,excluded): raise ApiError(409,"table_unavailable")
            a,b=datetime.fromisoformat(c["starts_at"]),datetime.fromisoformat(c["ends_at"])
            for other in candidates[:i]:
                if set(member_ids(c)) & set(member_ids(other)) and overlaps(a,b,datetime.fromisoformat(other["starts_at"]),datetime.fromisoformat(other["ends_at"])): raise ApiError(409,"table_unavailable")
        for old,c in zip(originals,candidates): old.clear(); old.update(c)
        response={"reservations":[public_res(r) for r in originals]}; STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response
    def handle_method(self,method):
        try:
            path=urlsplit(self.path).path
            if method=="GET" and path in PAGES:
                return self.send_bytes(200,(STATIC/"index.html").read_bytes(),"text/html; charset=utf-8")
            if method=="GET" and path in ASSETS:
                filename,content_type=ASSETS[path]
                return self.send_bytes(200,(STATIC/filename).read_bytes(),content_type)
            status,value=self.dispatch(method); self.send_json(status,value)
        except ApiError as e: self.send_json(e.status,{"error":{"code":e.code,"message":e.message}})
        except Exception: self.send_json(500,{"error":{"code":"internal_error","message":"internal server error"}})
    do_GET=lambda self:self.handle_method("GET")
    do_POST=lambda self:self.handle_method("POST")
    do_PATCH=lambda self:self.handle_method("PATCH")

class Server(ThreadingHTTPServer):
    request_queue_size=128

def main():
    port=int(os.environ.get("PORT","8080")); Server(("0.0.0.0",port),Handler).serve_forever()

if __name__=="__main__": main()
