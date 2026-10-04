import json
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
IDEM_MAX = 255

class ApiError(Exception):
    def __init__(self, status, code, message=None):
        self.status, self.code = status, code
        self.message = message or code.replace("_", " ")
        super().__init__(self.message)

def malformed(message="malformed request"):
    raise ApiError(400, "malformed_request", message)

def invalid(message="validation failed", code="validation_failed"):
    raise ApiError(422, code, message)

def require_object(value):
    if not isinstance(value, dict):
        malformed("JSON body must be an object")
    return value

def typed_equal(a, b):
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(typed_equal(x, y) for x, y in zip(a, b))
    return a == b

def json_clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

def field_str(body, name, *, required=True, max_len=None):
    if name not in body:
        if required: invalid(f"missing {name}")
        return None
    value = body[name]
    if not isinstance(value, str): malformed(f"{name} must be a string")
    if (required and value == "") or (max_len is not None and len(value) > max_len): invalid(f"invalid {name}")
    return value

def party_size(value):
    if type(value) is not int or value < 1: invalid("invalid party_size")
    return value

def idempotency_key(headers):
    value = headers.get("Idempotency-Key")
    if value is None or value == "": raise ApiError(400, "missing_idempotency_key")
    if len(value) > IDEM_MAX: invalid("idempotency key too long")
    return value
