import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime
from .validation import ApiError, json_clone, typed_equal, invalid
from .time_rules import overlaps, parse_hhmm, validate_slot, zone
from .seating import canonical_ids, member_ids, seating_fields
from .stage3 import normalize_stage3, policy_restaurant, validate_policy

def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()

def verify_password(password, salt, digest):
    try: actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32).hex()
    except Exception: return False
    return hmac.compare_digest(actual, digest)

def empty_state():
    return {"users": {}, "emails": {}, "tokens": {}, "restaurants": {}, "restaurant_order": [], "reservations": {}, "reservation_order": [], "receipts": {}, "next_user": 1, "next_reservation": 1,
            "policies": {}, "next_policy_version": {}, "restaurant_revisions": {}, "histories": {},
            "series": {}, "series_order": [], "reservation_series": {}, "next_series": 1}

def receipt_identity(user_id, method, path, key):
    return json.dumps([user_id, method, path, key], ensure_ascii=False, separators=(",", ":"))

class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = empty_state()
        self.generation = 0

    def receipt(self, user_id, method, path, key, body):
        item = self.data["receipts"].get(receipt_identity(user_id, method, path, key))
        if item is None: return None
        if not typed_equal(item["request"], body): raise ApiError(409, "idempotency_key_reuse")
        return json_clone(item["response"])

    def save_receipt(self, user_id, method, path, key, body, response):
        self.data["receipts"][receipt_identity(user_id, method, path, key)] = {"request": json_clone(body), "response": json_clone(response)}

    def export(self):
        with self.lock: return {"track": "tablekeeper", "format_version": 1, "state": json_clone(self.data)}

    def import_document(self, doc):
        if not isinstance(doc, dict) or doc.get("track") != "tablekeeper" or type(doc.get("format_version")) is not int or doc["format_version"] != 1 or not isinstance(doc.get("state"), dict): invalid("invalid import")
        candidate = json_clone(doc["state"])
        legacy = {"users","emails","tokens","restaurants","restaurant_order","reservations","reservation_order","receipts","next_user","next_reservation"}
        stage3 = {"policies","next_policy_version","restaurant_revisions","histories","series","series_order","reservation_series","next_series"}
        if not legacy <= set(candidate) or not set(candidate) <= legacy | stage3: invalid("invalid state")
        legacy_stage3 = not bool(set(candidate) & stage3)
        if not legacy_stage3 and not stage3 <= set(candidate): invalid("invalid state")
        normalize_stage3(candidate, legacy=legacy_stage3)
        if not all(isinstance(candidate[k], dict) for k in ("users","emails","tokens","restaurants","reservations","receipts")): invalid("invalid state")
        if not all(isinstance(candidate[k], list) for k in ("restaurant_order","reservation_order")): invalid("invalid state")
        if type(candidate["next_user"]) is not int or type(candidate["next_reservation"]) is not int or candidate["next_user"]<1 or candidate["next_reservation"]<1: invalid("invalid state")
        for uid, user in candidate["users"].items():
            if not isinstance(user, dict) or not uid or len(uid)>64 or user.get("id") != uid or not isinstance(user.get("email"),str) or not isinstance(user.get("display_name"),str) or not re.fullmatch(r"[0-9a-f]{32}",str(user.get("salt"))) or not re.fullmatch(r"[0-9a-f]{64}",str(user.get("password_hash"))): invalid("invalid state")
            if candidate["emails"].get(user["email"]) != uid: invalid("invalid state")
        if len(candidate["emails"]) != len(candidate["users"]) or any(uid not in candidate["users"] for uid in candidate["tokens"].values()) or any(not isinstance(token,str) or not token for token in candidate["tokens"]): invalid("invalid state")
        if len(candidate["restaurant_order"]) != len(candidate["restaurants"]) or set(candidate["restaurant_order"]) != set(candidate["restaurants"]): invalid("invalid state")
        if len(candidate["reservation_order"]) != len(candidate["reservations"]) or set(candidate["reservation_order"]) != set(candidate["reservations"]): invalid("invalid state")
        for restaurant_id, restaurant in candidate["restaurants"].items():
            if not isinstance(restaurant,dict) or not restaurant_id or len(restaurant_id)>64 or restaurant.get("id") != restaurant_id or not isinstance(restaurant.get("timezone"),str) or not isinstance(restaurant.get("tables"),list) or not isinstance(restaurant.get("opening_hours"),list): invalid("invalid state")
            if not isinstance(restaurant.get("name"),str): invalid("invalid state")
            zone(restaurant["timezone"])
            if type(restaurant.get("slot_minutes")) is not int or restaurant["slot_minutes"]<1 or type(restaurant.get("reservation_duration_minutes")) is not int or restaurant["reservation_duration_minutes"]<1 or type(restaurant.get("cancellation_cutoff_minutes")) is not int or restaurant["cancellation_cutoff_minutes"]<0: invalid("invalid state")
            if any(not isinstance(table,dict) or not isinstance(table.get("id"),str) or not table["id"] or len(table["id"])>64 or not isinstance(table.get("label"),str) or type(table.get("capacity")) is not int or table["capacity"]<1 for table in restaurant["tables"]): invalid("invalid state")
            if len({table["id"] for table in restaurant["tables"]})!=len(restaurant["tables"]): invalid("invalid state")
            combinations=restaurant.setdefault("combinable",[])
            if not isinstance(combinations,list): invalid("invalid state")
            table_ids={table["id"] for table in restaurant["tables"]}; seen_pairs=set()
            for pair in combinations:
                if not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(value,str) or value not in table_ids for value in pair) or pair[0]==pair[1] or frozenset(pair) in seen_pairs: invalid("invalid state")
                seen_pairs.add(frozenset(pair))
            weekdays=set()
            for hour in restaurant["opening_hours"]:
                if not isinstance(hour,dict) or hour.get("weekday") not in ("mon","tue","wed","thu","fri","sat","sun") or hour["weekday"] in weekdays: invalid("invalid state")
                if parse_hhmm(hour.get("opens"))>=parse_hhmm(hour.get("closes")): invalid("invalid state")
                weekdays.add(hour["weekday"])
        references=set()
        for rid, res in candidate["reservations"].items():
            if not isinstance(res,dict) or not rid or len(rid)>64 or res.get("reservation_id") != rid or res.get("user_id") not in candidate["users"] or res.get("restaurant_id") not in candidate["restaurants"] or res.get("status") not in ("confirmed","cancelled"): invalid("invalid state")
            ref=res.get("reference")
            if not isinstance(ref,str) or not re.fullmatch(r"[A-Z0-9]{6,12}",ref) or ref in references: invalid("invalid state")
            references.add(ref)
            restaurant=candidate["restaurants"][res["restaurant_id"]]
            if "table_ids" in res:
                ids=res["table_ids"]
                if not isinstance(ids,list) or not 1<=len(ids)<=2 or any(not isinstance(value,str) or not value for value in ids) or len(set(ids))!=len(ids): invalid("invalid state")
                if (len(ids)==1 and res.get("table_id")!=ids[0]) or (len(ids)==2 and "table_id" in res): invalid("invalid state")
            else:
                if not isinstance(res.get("table_id"),str): invalid("invalid state")
                ids=[res["table_id"]]
            try:
                effective=policy_restaurant(restaurant,res["accepted_terms"])
                ids,capacity=canonical_ids(effective,ids)
            except ApiError: invalid("invalid state")
            if type(res.get("party_size")) is not int or not 1<=res["party_size"]<=capacity: invalid("invalid state")
            res.pop("table_id",None); res.update(seating_fields(ids))
            try:
                start,end=validate_slot(effective,res["starts_at_local"])
                if res.get("starts_at")!=start.isoformat(timespec="seconds") or res.get("ends_at")!=end.isoformat(timespec="seconds") or not isinstance(res.get("created_at"),str): invalid("invalid state")
                created=datetime.fromisoformat(res["created_at"])
                if created.tzinfo is None: invalid("invalid state")
            except (KeyError,ValueError,TypeError,ApiError): invalid("invalid state")
        confirmed=[r for r in candidate["reservations"].values() if r["status"]=="confirmed"]
        for i,a in enumerate(confirmed):
            for b in confirmed[:i]:
                if a["restaurant_id"]==b["restaurant_id"] and set(member_ids(a)) & set(member_ids(b)) and overlaps(datetime.fromisoformat(a["starts_at"]),datetime.fromisoformat(a["ends_at"]),datetime.fromisoformat(b["starts_at"]),datetime.fromisoformat(b["ends_at"])): invalid("invalid state")
        if set(candidate["policies"])!=set(candidate["restaurants"]) or set(candidate["next_policy_version"])!=set(candidate["restaurants"]) or set(candidate["restaurant_revisions"])!=set(candidate["restaurants"]): invalid("invalid state")
        for rid, policies in candidate["policies"].items():
            if not isinstance(policies,list): invalid("invalid state")
            for expected, policy in enumerate(policies,1):
                if not isinstance(policy,dict) or policy.get("policy_version")!=expected: invalid("invalid state")
                try:
                    if validate_policy(policy,candidate["restaurants"][rid],expected)!=policy: invalid("invalid state")
                except ApiError: invalid("invalid state")
            if candidate["next_policy_version"][rid]!=len(policies)+1 or type(candidate["restaurant_revisions"][rid]) is not int or candidate["restaurant_revisions"][rid]<0: invalid("invalid state")
        if set(candidate["histories"])!=set(candidate["reservations"]): invalid("invalid state")
        for reservation_id, entries in candidate["histories"].items():
            reservation=candidate["reservations"][reservation_id]
            if not isinstance(entries,list) or not entries: invalid("invalid state")
            previous_at=None
            for index,entry in enumerate(entries,1):
                if not isinstance(entry,dict) or entry.get("seq")!=index or entry.get("event") not in ("created","changed","cancelled") or not isinstance(entry.get("changes"),list) or type(entry.get("revision")) is not int or not isinstance(entry.get("accepted_terms"),dict): invalid("invalid state")
                try: at=datetime.fromisoformat(entry["at"])
                except (KeyError,TypeError,ValueError): invalid("invalid state")
                if at.tzinfo is None or (previous_at is not None and at<previous_at): invalid("invalid state")
                previous_at=at
            if entries[0]["event"]!="created" or entries[-1]["revision"]>reservation["revision"] or (reservation["status"]=="cancelled" and entries[-1]["event"]!="cancelled"): invalid("invalid state")
        if len(candidate["series_order"])!=len(candidate["series"]) or set(candidate["series_order"])!=set(candidate["series"]): invalid("invalid state")
        expected_links={}
        for series_id,series in candidate["series"].items():
            if not isinstance(series,dict) or series.get("series_id")!=series_id or series.get("user_id") not in candidate["users"] or series.get("restaurant_id") not in candidate["restaurants"] or type(series.get("revision")) is not int or series["revision"]<1 or type(series.get("interval_weeks")) is not int or not 1<=series["interval_weeks"]<=4: invalid("invalid state")
            occurrences=series.get("occurrences")
            if not isinstance(occurrences,list) or not 2<=len(occurrences)<=12: invalid("invalid state")
            for index,item in enumerate(occurrences):
                if not isinstance(item,dict) or item.get("index")!=index or type(item.get("exception")) is not bool or item.get("reservation_id") not in candidate["reservations"]: invalid("invalid state")
                reservation=candidate["reservations"][item["reservation_id"]]
                if reservation["user_id"]!=series["user_id"] or reservation["restaurant_id"]!=series["restaurant_id"] or item["reservation_id"] in expected_links: invalid("invalid state")
                expected_links[item["reservation_id"]]=series_id
        if candidate["reservation_series"]!=expected_links or type(candidate["next_series"]) is not int or candidate["next_series"]<1: invalid("invalid state")
        for receipt_key, receipt in candidate["receipts"].items():
            try: pieces=json.loads(receipt_key)
            except (TypeError,ValueError): invalid("invalid state")
            if not isinstance(pieces,list) or len(pieces)!=4 or any(not isinstance(x,str) for x in pieces) or receipt_identity(*pieces)!=receipt_key or pieces[0] not in candidate["users"] or pieces[1]!="POST" or not 1<=len(pieces[3])<=255 or not isinstance(receipt,dict) or not isinstance(receipt.get("request"),dict) or not isinstance(receipt.get("response"),dict): invalid("invalid state")
            response=receipt["response"]
            if pieces[2].startswith("/restaurants/") and pieces[2].endswith("/policies"): continue
            if pieces[2]=="/series": continue
            if pieces[2] not in ("/reservations","/reservation-moves"): invalid("invalid state")
            entries=[response] if pieces[2]=="/reservations" else response.get("reservations")
            if not isinstance(entries,list) or (pieces[2]=="/reservations" and len(entries)!=1) or (pieces[2]=="/reservation-moves" and not 1<=len(entries)<=8): invalid("invalid state")
            for original in entries:
                if not isinstance(original,dict): invalid("invalid state")
                booking=candidate["reservations"].get(original.get("reservation_id"))
                if booking is None or booking["user_id"]!=pieces[0]: invalid("invalid state")
                for immutable in ("reservation_id","reference","restaurant_id","created_at"):
                    if original.get(immutable)!=booking[immutable]: invalid("invalid state")
                if original.get("status") not in ("confirmed","cancelled") or type(original.get("party_size")) is not int or original["party_size"]<1: invalid("invalid state")
                if "table_ids" in original:
                    old_ids=original["table_ids"]
                    if not isinstance(old_ids,list) or not 1<=len(old_ids)<=2 or any(not isinstance(value,str) for value in old_ids) or len(set(old_ids))!=len(old_ids): invalid("invalid state")
                    if (len(old_ids)==1 and original.get("table_id")!=old_ids[0]) or (len(old_ids)==2 and "table_id" in original): invalid("invalid state")
                elif not isinstance(original.get("table_id"),str): invalid("invalid state")
                if not isinstance(original.get("starts_at_local"),str): invalid("invalid state")
                for timestamp in ("starts_at","ends_at"):
                    if not isinstance(original.get(timestamp),str) or datetime.fromisoformat(original[timestamp]).tzinfo is None: invalid("invalid state")
        with self.lock:
            self.data = candidate
            self.generation += 1

    def reset(self, candidate):
        with self.lock:
            self.data = candidate
            self.generation += 1

STORE = Store()
