import copy
import json
import threading
import unittest
import urllib.error
import urllib.request

from app.server import Handler, Server, public_res, validate_fixture
from app.state import Store, receipt_identity
from app.validation import ApiError


FIXTURE = {
    "users": [{"id": "u\x1fsep", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
    "restaurants": [{"id": "r", "name": "R", "timezone": "UTC", "slot_minutes": 30,
                     "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
                     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
                     "tables": [{"id": "t", "label": "T", "capacity": 4}]}],
    "reservations": [],
}


class QARegressions(unittest.TestCase):
    def test_fixture_wrong_types_are_malformed(self):
        changes = [
            lambda f: f.update(users="wrong"),
            lambda f: f["users"].append("wrong"),
            lambda f: f["users"][0].update(email=1),
            lambda f: f["restaurants"][0].update(slot_minutes=True),
            lambda f: f["restaurants"][0].update(opening_hours="wrong"),
            lambda f: f["restaurants"][0]["opening_hours"][0].update(opens=1),
            lambda f: f["restaurants"][0]["tables"][0].update(capacity="4"),
            lambda f: f.update(reservations=[{"id": 1, "reference": "ABC12345", "user_id": f["users"][0]["id"],
                                               "restaurant_id": "r", "table_id": "t", "starts_at_local": "2027-09-23T19:00",
                                               "party_size": 2}]),
        ]
        for change in changes:
            fixture = copy.deepcopy(FIXTURE)
            change(fixture)
            with self.subTest(fixture=fixture), self.assertRaises(ApiError) as raised:
                validate_fixture(fixture)
            self.assertEqual((raised.exception.status, raised.exception.code), (400, "malformed_request"))

    def test_receipt_identity_round_trips_opaque_id(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture["reservations"].append({"id": "res_1", "reference": "ABC12345", "user_id": fixture["users"][0]["id"],
                                        "restaurant_id": "r", "table_id": "t", "starts_at_local": "2027-09-23T19:00",
                                        "party_size": 2})
        store = Store()
        store.reset(validate_fixture(fixture))
        user = FIXTURE["users"][0]["id"]
        body = {"opaque": True}
        response = public_res(store.data["reservations"]["res_1"])
        key = receipt_identity(user, "POST", "/reservations", "key\x1fpart")
        self.assertEqual(json.loads(key), [user, "POST", "/reservations", "key\x1fpart"])
        store.save_receipt(user, "POST", "/reservations", "key\x1fpart", body, response)
        self.assertEqual(store.receipt(user, "POST", "/reservations", "key\x1fpart", body), response)
        restored = Store()
        restored.import_document(store.export())
        self.assertEqual(restored.export(), store.export())
        self.assertEqual(restored.receipt(user, "POST", "/reservations", "key\x1fpart", body), response)

    def test_seeded_party_size_precedence_and_reset_rollback(self):
        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(method, path, body=None):
            raw = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=raw, method=method)
            try:
                response = urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                payload = response.read()
                return response.status, json.loads(payload) if payload else None

        try:
            self.assertEqual(call("POST", "/_test/reset", FIXTURE)[0], 204)
            before = call("GET", "/_test/export")[1]
            seed = {"id": "res_1", "reference": "ABC12345", "user_id": FIXTURE["users"][0]["id"],
                    "restaurant_id": "r", "table_id": "t", "starts_at_local": "2027-09-23T19:00",
                    "party_size": 2}
            for bad in ("2", True, 2.5):
                fixture = copy.deepcopy(FIXTURE)
                fixture["reservations"] = [{**seed, "party_size": bad}]
                with self.subTest(party_size=bad):
                    status, error = call("POST", "/_test/reset", fixture)
                    self.assertEqual((status, error["error"]["code"]), (422, "validation_failed"))
                    self.assertEqual(call("GET", "/_test/export")[1], before)
            fixture["reservations"] = [{**seed, "id": 7}]
            status, error = call("POST", "/_test/reset", fixture)
            self.assertEqual((status, error["error"]["code"]), (400, "malformed_request"))
            self.assertEqual(call("GET", "/_test/export")[1], before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_unknown_method_is_json_4xx(self):
        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/health", method="PUT")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            error = raised.exception
            self.assertEqual(error.code, 405)
            self.assertIn("application/json", error.headers["Content-Type"])
            self.assertEqual(json.load(error)["error"]["code"], "method_not_allowed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
