import json, os, re, secrets, threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from .state import STORE, empty_state, hash_password, verify_password
from .seating import canonical_ids, input_ids, member_ids, seating_fields
from .validation import ApiError, EMAIL_RE, field_str, idempotency_key, invalid, malformed, party_size, require_object, json_clone
from .time_rules import UTC, enumerate_slots, iso, overlaps, parse_date, validate_slot, zone
from .stage3 import (accepted_terms, append_history, history_changes, normalize_stage3,
                     policy_restaurant, policy_zero, select_policy, validate_policy)
from .stage4 import (closed_members, normalize_stage4, parse_instant, plan_reassignment)

HASH_LIMIT = threading.BoundedSemaphore(8)
STATIC = Path(__file__).with_name("static")
PAGES = {"/", "/signup", "/login", "/lookup"}
ASSETS = {"/assets/app.css": ("app.css", "text/css; charset=utf-8"),
          "/assets/app.js": ("app.js", "text/javascript; charset=utf-8")}
API_PREFIXES = ("/health", "/_test", "/auth", "/restaurants", "/availability",
                "/reservations", "/reservation-moves", "/series")

def public_res(r):
    result = {k: r[k] for k in ("reservation_id","reference","restaurant_id","party_size","status","starts_at_local","starts_at","ends_at","created_at")}
    result.update(seating_fields(member_ids(r)))
    result["revision"] = r["revision"]
    result["accepted_terms"] = json_clone(r["accepted_terms"])
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
    policy = select_policy(data, rest, starts_local[:10] if isinstance(starts_local, str) else starts_local)
    effective = policy_restaurant(rest, policy)
    ids, capacity = canonical_ids(effective, table_ids)
    size = party_size(size)
    start, end = validate_slot(effective, starts_local)
    if size > capacity: raise ApiError(422, "party_exceeds_capacity")
    result = dict(existing or {})
    result.pop("table_id", None)
    result.update(restaurant_id=restaurant_id, **seating_fields(ids), party_size=size, starts_at_local=starts_local, starts_at=iso(start), ends_at=iso(end), accepted_terms=accepted_terms(policy))
    return result

def requested_change(data, existing, body):
    ids = input_ids(body, existing)
    restaurant = data["restaurants"][existing["restaurant_id"]]
    ids, _capacity = canonical_ids(restaurant, ids)
    starts_local = body.get("starts_at_local", existing["starts_at_local"])
    if not isinstance(starts_local, str): malformed("starts_at_local must be string")
    size = party_size(body.get("party_size", existing["party_size"]))
    unchanged = (ids == member_ids(existing) and
                 starts_local == existing["starts_at_local"] and
                 size == existing["party_size"])
    return ids, starts_local, size, unchanged

def has_conflict(data, candidate, excluded_ids=()):
    a, b = datetime.fromisoformat(candidate["starts_at"]), datetime.fromisoformat(candidate["ends_at"])
    if closed_members(data,candidate["restaurant_id"],member_ids(candidate),a,b): return True
    for rid, other in data["reservations"].items():
        if rid in excluded_ids or other["status"] != "confirmed": continue
        if other["restaurant_id"] == candidate["restaurant_id"] and set(member_ids(other)) & set(member_ids(candidate)) and overlaps(a,b,datetime.fromisoformat(other["starts_at"]),datetime.fromisoformat(other["ends_at"])): return True
    return False

def check_cutoff(data, res):
    start = datetime.fromisoformat(res["starts_at"]).astimezone(UTC)
    if datetime.now(UTC) >= start - timedelta(minutes=res["accepted_terms"]["cancellation_cutoff_minutes"]): raise ApiError(409, "cutoff_passed")

def fixture_type(obj, key, expected):
    if key not in obj: invalid(f"missing {key}")
    value = obj[key]
    if type(value) is not expected: malformed(f"{key} has wrong JSON type")
    return value

def validate_json_unicode(value):
    if isinstance(value, str):
        try: value.encode("utf-8")
        except UnicodeEncodeError: malformed("invalid unicode")
    elif isinstance(value, list):
        for item in value: validate_json_unicode(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_json_unicode(key); validate_json_unicode(item)

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
        managers=rest.get("manager_user_ids",[])
        if type(managers) is not list: malformed("manager_user_ids has wrong JSON type")
        if any(type(value) is not str for value in managers) or len(set(managers))!=len(managers) or any(value not in data["users"] for value in managers): invalid("invalid manager_user_ids")
        saved=json_clone(rest); saved["combinable"]=json_clone(combinations); saved["manager_user_ids"]=json_clone(managers)
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
        candidate.update(reservation_id=rid,reference=ref,user_id=uid,status=status,created_at=datetime.now(UTC).isoformat(timespec="seconds"),revision=1)
        if status=="confirmed" and has_conflict(data,candidate): invalid("overlapping seed")
        data["reservations"][rid]=candidate; data["reservation_order"].append(rid); refs.add(ref)
    while f"u_{data['next_user']}" in data["users"]: data["next_user"]+=1
    while f"res_{data['next_reservation']}" in data["reservations"]: data["next_reservation"]+=1
    normalize_stage3(data, legacy=True)
    normalize_stage4(data)
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
            value=json.loads(raw.decode("utf-8"),parse_constant=lambda _: malformed())
            validate_json_unicode(value)
            return value
        except ApiError: raise
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
        policy_match=re.fullmatch(r"/restaurants/([^/]+)/policies",path)
        replan_preview=re.fullmatch(r"/restaurants/([^/]+)/replans",path)
        replan_apply=re.fullmatch(r"/restaurants/([^/]+)/replans/([^/]+)/apply",path)
        series_amend=re.fullmatch(r"/series/([^/]+)/amend",path)
        if method=="GET" and policy_match:
            rid=unquote(policy_match.group(1))
            with STORE.lock:
                if rid not in STORE.data["restaurants"]: raise ApiError(404,"not_found")
                return 200,{"policies":json_clone(STORE.data["policies"].get(rid,[]))}
        if method=="GET" and path.startswith("/restaurants/"):
            rid=unquote(path[len("/restaurants/"):])
            if len(rid)>64: invalid("invalid restaurant id")
            with STORE.lock:
                rest=STORE.data["restaurants"].get(rid)
                if rest is None: raise ApiError(404,"not_found")
                return 200,json_clone(rest)
        if method=="GET" and path=="/availability": return self.availability(parse_qs(split.query,keep_blank_values=True))
        private=re.fullmatch(r"/reservations/([^/]+)/(history|decision)",path)
        series_get=re.fullmatch(r"/series/([^/]+)",path)
        if method=="GET" and (private or series_get):
            with STORE.lock:
                try: uid=authenticate(self.headers,STORE.data)
                except ApiError: raise ApiError(404,"not_found")
                if private:
                    res=by_reference(STORE.data,unquote(private.group(1)))
                    if res is None or res["user_id"]!=uid: raise ApiError(404,"not_found")
                    if private.group(2)=="history": return 200,{"reference":res["reference"],"entries":json_clone(STORE.data["histories"][res["reservation_id"]])}
                    return 200,{"reference":res["reference"],"revision":res["revision"],"accepted_terms":json_clone(res["accepted_terms"])}
                series=STORE.data["series"].get(unquote(series_get.group(1)))
                if series is None or series["user_id"]!=uid: raise ApiError(404,"not_found")
                return 200,self.series_response(series)
        body=None
        mutation = path in ("/reservations","/reservation-moves","/series") or any((policy_match,replan_preview,replan_apply,series_amend))
        if method=="PATCH" or (method=="POST" and mutation):
            with STORE.lock: authenticate(self.headers,STORE.data)
            body=require_object(self.body())
        with STORE.lock:
            uid=authenticate(self.headers,STORE.data)
            if method=="POST" and path=="/reservations": return self.create(uid,body,path)
            if method=="POST" and path=="/reservation-moves": return self.moves(uid,body,path)
            if method=="POST" and path=="/series": return self.create_series(uid,body,path)
            if method=="POST" and policy_match: return self.publish_policy(uid,unquote(policy_match.group(1)),body,path)
            if method=="POST" and replan_preview: return self.preview_replan(uid,unquote(replan_preview.group(1)),body,path)
            if method=="POST" and replan_apply: return self.apply_replan(uid,unquote(replan_apply.group(1)),unquote(replan_apply.group(2)),body,path)
            if method=="POST" and series_amend: return self.amend_series(uid,unquote(series_amend.group(1)),body,path)
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
                    check_cutoff(STORE.data,res); res["status"]="cancelled"; res["revision"]+=1
                    append_history(STORE.data,res,"cancelled",[]); self.touch_series(res,False)
                    STORE.data["restaurant_revisions"][res["restaurant_id"]]+=1
                    return 200,public_res(res)
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
        explain="explain" in q
        if explain and (len(q["explain"])!=1 or q["explain"][0]!="true"): invalid("invalid explain")
        with STORE.lock:
            rest=STORE.data["restaurants"].get(q["restaurant_id"][0])
            if rest is None: raise ApiError(404,"not_found")
            policy=select_policy(STORE.data,rest,day); effective=policy_restaurant(rest,policy)
            slots=[]
            for start,end in enumerate_slots(effective,day):
                avail=[]; options=[]; explanations=[]
                for table in effective["tables"]:
                    c={"restaurant_id":rest["id"],"table_ids":[table["id"]],"starts_at":iso(start),"ends_at":iso(end)}
                    cap=table["capacity"]>=size; free=not has_conflict(STORE.data,c)
                    if cap and free:
                        avail.append(table["id"]); options.append({"table_ids":[table["id"]],"capacity":table["capacity"]})
                    if explain: explanations.append({"table_id":table["id"],"policy_version":policy["policy_version"],"available":cap and free,"rules":[{"rule":"capacity","holds":cap},{"rule":"no_overlap","holds":free}]})
                for pair in effective.get("combinable",[]):
                    capacity=sum(table_for(effective,table_id)["capacity"] for table_id in pair)
                    c={"restaurant_id":rest["id"],"table_ids":pair,"starts_at":iso(start),"ends_at":iso(end)}
                    if capacity>=size and not has_conflict(STORE.data,c): options.append({"table_ids":list(pair),"capacity":capacity})
                slot={"starts_at_local":start.strftime("%Y-%m-%dT%H:%M"),"starts_at":iso(start),"available_table_ids":avail,"available_options":options}
                if explain: slot["explain"]=explanations
                slots.append(slot)
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
        c.update(reservation_id=rid,reference=ref,user_id=uid,status="confirmed",created_at=datetime.now(UTC).isoformat(timespec="seconds"),revision=1)
        STORE.data["reservations"][rid]=c; STORE.data["reservation_order"].append(rid)
        append_history(STORE.data,c,"created",history_changes({},c,True)); STORE.data["restaurant_revisions"][c["restaurant_id"]]+=1
        response=public_res(c); STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response
    def patch(self,res,body):
        if res["status"]=="cancelled": raise ApiError(409,"reservation_cancelled")
        if "expected_revision" in body:
            expected=body["expected_revision"]
            if type(expected) is not int or expected<1: invalid("invalid expected_revision")
            if expected!=res["revision"]: raise ApiError(409,"stale_revision")
        check_cutoff(STORE.data,res)
        ids,starts_local,size,unchanged=requested_change(STORE.data,res,body)
        if unchanged: return 200,public_res(res)
        c=build_candidate(STORE.data,res,res["restaurant_id"],ids,starts_local,size)
        changes=history_changes(res,c)
        if has_conflict(STORE.data,c,{res["reservation_id"]}): raise ApiError(409,"table_unavailable")
        c["revision"]=res["revision"]+1
        res.clear(); res.update(c); append_history(STORE.data,res,"changed",changes); self.touch_series(res,True)
        STORE.data["restaurant_revisions"][res["restaurant_id"]]+=1
        return 200,public_res(res)
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
        originals=[]; finals=[]; changes_by_move=[]; restaurant_id=None
        for ref,m in zip(refs,moves):
            old=by_reference(STORE.data,ref)
            if old is None or old["user_id"]!=uid: raise ApiError(404,"not_found")
            if restaurant_id is None: restaurant_id=old["restaurant_id"]
            elif old["restaurant_id"]!=restaurant_id: invalid("different restaurants")
            originals.append(old)
            if old["status"]=="cancelled": raise ApiError(409,"reservation_cancelled")
            if "expected_revision" in m:
                expected=m["expected_revision"]
                if type(expected) is not int or expected<1: invalid("invalid expected_revision")
                if expected!=old["revision"]: raise ApiError(409,"stale_revision")
            check_cutoff(STORE.data,old)
            ids,starts_local,size,unchanged=requested_change(STORE.data,old,m)
            final=old if unchanged else build_candidate(STORE.data,old,old["restaurant_id"],ids,starts_local,size)
            finals.append(final)
            changes_by_move.append([] if unchanged else history_changes(old,final))
        excluded={r["reservation_id"] for r in originals}
        for i,c in enumerate(finals):
            if has_conflict(STORE.data,c,excluded): raise ApiError(409,"table_unavailable")
            a,b=datetime.fromisoformat(c["starts_at"]),datetime.fromisoformat(c["ends_at"])
            for other in finals[:i]:
                if set(member_ids(c)) & set(member_ids(other)) and overlaps(a,b,datetime.fromisoformat(other["starts_at"]),datetime.fromisoformat(other["ends_at"])): raise ApiError(409,"table_unavailable")
        changed=[(old,final,changes) for old,final,changes in zip(originals,finals,changes_by_move) if changes]
        touched_series=set()
        for old,c,changes in changed:
            c["revision"]=old["revision"]+1; old.clear(); old.update(c); append_history(STORE.data,old,"changed",changes)
            series_id=STORE.data["reservation_series"].get(old["reservation_id"])
            if series_id:
                occurrence=next(item for item in STORE.data["series"][series_id]["occurrences"] if item["reservation_id"]==old["reservation_id"])
                occurrence["exception"]=True; touched_series.add(series_id)
        for series_id in touched_series: STORE.data["series"][series_id]["revision"]+=1
        if changed: STORE.data["restaurant_revisions"][restaurant_id]+=1
        response={"reservations":[public_res(r) for r in originals]}; STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response

    def touch_series(self,res,exception):
        series_id=STORE.data["reservation_series"].get(res["reservation_id"])
        if not series_id: return
        series=STORE.data["series"][series_id]
        occurrence=next(item for item in series["occurrences"] if item["reservation_id"]==res["reservation_id"])
        if exception: occurrence["exception"]=True
        series["revision"]+=1

    def publish_policy(self,uid,rid,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        restaurant=STORE.data["restaurants"].get(rid)
        if restaurant is None: raise ApiError(404,"not_found")
        if uid not in restaurant.get("manager_user_ids",[]): raise ApiError(403,"forbidden")
        version=STORE.data["next_policy_version"][rid]
        policy=validate_policy(body,restaurant,version)
        STORE.data["policies"][rid].append(policy); STORE.data["next_policy_version"][rid]=version+1
        STORE.data["restaurant_revisions"][rid]+=1
        response=json_clone(policy); STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response

    def series_response(self,series):
        return {"series_id":series["series_id"],"revision":series["revision"],"interval_weeks":series["interval_weeks"],
                "occurrences":[{"index":item["index"],"reference":STORE.data["reservations"][item["reservation_id"]]["reference"],
                                "exception":item["exception"],"reservation":public_res(STORE.data["reservations"][item["reservation_id"]])}
                               for item in series["occurrences"]]}

    def create_series(self,uid,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        ref=body.get("anchor_reference"); count=body.get("count"); interval=body.get("interval_weeks")
        if not isinstance(ref,str) or type(count) is not int or type(interval) is not int or not 2<=count<=12 or not 1<=interval<=4: invalid("invalid series")
        anchor=by_reference(STORE.data,ref)
        if anchor is None or anchor["user_id"]!=uid: raise ApiError(404,"not_found")
        if anchor["status"]=="cancelled": raise ApiError(409,"reservation_cancelled")
        if anchor["reservation_id"] in STORE.data["reservation_series"]: raise ApiError(409,"already_in_series")
        check_cutoff(STORE.data,anchor)
        working=json_clone(STORE.data); anchor=working["reservations"][anchor["reservation_id"]]
        series_id=f"series_{working['next_series']}"
        while series_id in working["series"]:
            working["next_series"]+=1; series_id=f"series_{working['next_series']}"
        working["next_series"]+=1
        occurrences=[{"index":0,"reservation_id":anchor["reservation_id"],"exception":False}]
        anchor_dt=datetime.strptime(anchor["starts_at_local"],"%Y-%m-%dT%H:%M")
        refs={item["reference"] for item in working["reservations"].values()}
        for index in range(1,count):
            try: starts=(anchor_dt+timedelta(weeks=index*interval)).strftime("%Y-%m-%dT%H:%M")
            except OverflowError: invalid("invalid series")
            c=build_candidate(working,None,anchor["restaurant_id"],member_ids(anchor),starts,anchor["party_size"])
            if has_conflict(working,c): raise ApiError(409,"table_unavailable")
            while f"res_{working['next_reservation']}" in working["reservations"]: working["next_reservation"]+=1
            rid=f"res_{working['next_reservation']}"; working["next_reservation"]+=1
            while True:
                new_ref=''.join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(8))
                if new_ref not in refs: refs.add(new_ref); break
            c.update(reservation_id=rid,reference=new_ref,user_id=uid,status="confirmed",created_at=datetime.now(UTC).isoformat(timespec="seconds"),revision=1)
            working["reservations"][rid]=c; working["reservation_order"].append(rid)
            append_history(working,c,"created",history_changes({},c,True)); occurrences.append({"index":index,"reservation_id":rid,"exception":False})
        series={"series_id":series_id,"user_id":uid,"restaurant_id":anchor["restaurant_id"],"revision":1,"interval_weeks":interval,"occurrences":occurrences}
        working["series"][series_id]=series; working["series_order"].append(series_id)
        for occurrence in occurrences: working["reservation_series"][occurrence["reservation_id"]]=series_id
        working["restaurant_revisions"][anchor["restaurant_id"]]+=1
        STORE.data=working; response=self.series_response(series); STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response

    def preview_replan(self,uid,rid,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        restaurant=STORE.data["restaurants"].get(rid)
        if restaurant is None: raise ApiError(404,"not_found")
        if uid not in restaurant.get("manager_user_ids",[]): raise ApiError(403,"forbidden")
        table_id=field_str(body,"table_id",max_len=64)
        if table_for(restaurant,table_id) is None: raise ApiError(404,"not_found")
        if "from" not in body or "to" not in body: invalid("invalid closure interval")
        start,end=parse_instant(body["from"]),parse_instant(body["to"])
        if not start<end: invalid("invalid closure interval")
        closure={"table_id":table_id,"from":body["from"],"to":body["to"]}
        assignments,moved,unused=plan_reassignment(STORE.data,restaurant,closure)
        while f"plan_{STORE.data['next_plan']}" in STORE.data["replans"]: STORE.data["next_plan"]+=1
        plan_id=f"plan_{STORE.data['next_plan']}"; STORE.data["next_plan"]+=1
        revision=STORE.data["restaurant_revisions"][rid]
        stored={"plan_id":plan_id,"restaurant_id":rid,"restaurant_revision":revision,
                "closure":json_clone(closure),"assignments":json_clone(assignments),
                "moved_count":moved,"unused_seats":unused,"applied":False}
        STORE.data["replans"][plan_id]=stored
        response={"plan_id":plan_id,"restaurant_revision":revision,"closure":json_clone(closure),
                  "assignments":[{key:value for key,value in assignment.items() if key!="reservation_id"} for assignment in assignments],
                  "moved_count":moved,"unused_seats":unused}
        STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response

    def apply_replan(self,uid,rid,plan_id,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        restaurant=STORE.data["restaurants"].get(rid)
        if restaurant is None: raise ApiError(404,"not_found")
        if uid not in restaurant.get("manager_user_ids",[]): raise ApiError(403,"forbidden")
        plan=STORE.data["replans"].get(plan_id)
        if plan is None or plan["restaurant_id"]!=rid: raise ApiError(404,"not_found")
        if plan["applied"]: raise ApiError(409,"plan_already_applied")
        if STORE.data["restaurant_revisions"][rid]!=plan["restaurant_revision"]: raise ApiError(409,"stale_plan")
        assignments,moved,unused=plan_reassignment(STORE.data,restaurant,plan["closure"])
        if (assignments!=plan["assignments"] or moved!=plan["moved_count"] or
                unused!=plan["unused_seats"]):
            raise ApiError(409,"stale_plan")
        touched_series=set(); reservations=[]
        for assignment in plan["assignments"]:
            reservation=STORE.data["reservations"][assignment["reservation_id"]]
            if assignment["changed"]:
                before=list(member_ids(reservation)); after=list(assignment["table_ids"])
                reservation.pop("table_id",None); reservation.pop("table_ids",None); reservation.update(seating_fields(after)); reservation["revision"]+=1
                entry=append_history(STORE.data,reservation,"reassigned",[{"field":"table_ids","from":before,"to":after}])
                entry["plan_id"]=plan_id
                series_id=STORE.data["reservation_series"].get(reservation["reservation_id"])
                if series_id: touched_series.add(series_id)
            reservations.append(reservation)
        for series_id in touched_series: STORE.data["series"][series_id]["revision"]+=1
        closure=json_clone(plan["closure"]); closure["plan_id"]=plan_id; STORE.data["closures"][rid].append(closure)
        STORE.data["restaurant_revisions"][rid]+=1; plan["applied"]=True
        response={"plan_id":plan_id,"restaurant_revision":STORE.data["restaurant_revisions"][rid],
                  "reservations":[public_res(reservation) for reservation in reservations]}
        STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response

    def amend_series(self,uid,series_id,body,path):
        key=idempotency_key(self.headers); replay=STORE.receipt(uid,"POST",path,key,body)
        if replay is not None: return 200,replay
        series=STORE.data["series"].get(series_id)
        if series is None or series["user_id"]!=uid: raise ApiError(404,"not_found")
        expected=body.get("expected_revision"); from_index=body.get("from_index"); local_time=body.get("local_time")
        if type(expected) is not int or expected<1 or type(from_index) is not int or not 0<=from_index<len(series["occurrences"]) or not isinstance(local_time,str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",local_time): invalid("invalid series amendment")
        if expected!=series["revision"]: raise ApiError(409,"stale_revision")
        working=json_clone(STORE.data); target=working["series"][series_id]
        changes=[]
        for occurrence in target["occurrences"]:
            if occurrence["index"]<from_index or occurrence["exception"]: continue
            reservation=working["reservations"][occurrence["reservation_id"]]
            if reservation["status"]=="cancelled": continue
            starts=reservation["starts_at_local"][:10]+"T"+local_time
            if starts==reservation["starts_at_local"]: continue
            check_cutoff(working,reservation)
            candidate=build_candidate(working,reservation,reservation["restaurant_id"],member_ids(reservation),starts,reservation["party_size"])
            changes.append((reservation,candidate,history_changes(reservation,candidate)))
        excluded={reservation["reservation_id"] for reservation,_candidate,_history in changes}
        for index,(reservation,candidate,_history) in enumerate(changes):
            if has_conflict(working,candidate,excluded): raise ApiError(409,"table_unavailable")
            start,end=datetime.fromisoformat(candidate["starts_at"]),datetime.fromisoformat(candidate["ends_at"])
            for _other,other,_changes in changes[:index]:
                if set(member_ids(candidate)) & set(member_ids(other)) and overlaps(start,end,datetime.fromisoformat(other["starts_at"]),datetime.fromisoformat(other["ends_at"])): raise ApiError(409,"table_unavailable")
        if changes:
            for reservation,candidate,history in changes:
                candidate["revision"]=reservation["revision"]+1; reservation.clear(); reservation.update(candidate)
                append_history(working,reservation,"changed",history)
            target["revision"]+=1; working["restaurant_revisions"][target["restaurant_id"]]+=1
        STORE.data=working; response=self.series_response(STORE.data["series"][series_id]); STORE.save_receipt(uid,"POST",path,key,body,response); return 201,response
    def handle_method(self,method):
        try:
            path=urlsplit(self.path).path
            browser_navigation = "text/html" in self.headers.get("Accept", "")
            if method=="GET" and (path in PAGES or
                                  (browser_navigation and not path.startswith(API_PREFIXES) and path not in ASSETS)):
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
