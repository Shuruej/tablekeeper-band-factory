import unittest
from datetime import datetime, timezone
from app.time_rules import parse_local, absolute_end, overlaps
from app.validation import typed_equal

class Invariants(unittest.TestCase):
    def test_typed_json(self):
        self.assertFalse(typed_equal(True, 1)); self.assertTrue(typed_equal(1,1.0)); self.assertTrue(typed_equal({"b":2,"a":1},{"a":1,"b":2})); self.assertFalse(typed_equal([1,2],[2,1]))
    def test_half_open(self):
        a=datetime.fromisoformat("2026-01-01T19:00:00+00:00"); b=datetime.fromisoformat("2026-01-01T20:30:00+00:00"); c=datetime.fromisoformat("2026-01-01T22:00:00+00:00")
        self.assertFalse(overlaps(a,b,b,c)); self.assertTrue(overlaps(a,b,datetime.fromisoformat("2026-01-01T20:00:00+00:00"),c))
    def test_gaps_and_folds(self):
        cases=[("Europe/Berlin","2026-03-29T02:30"),("America/New_York","2026-03-08T02:30")]
        for z,t in cases:
            with self.assertRaises(Exception): parse_local(t,z)
        self.assertEqual(parse_local("2026-10-25T02:30","Europe/Berlin").utcoffset().total_seconds(),7200)
        self.assertEqual(parse_local("2026-11-01T01:30","America/New_York").utcoffset().total_seconds(),-14400)
    def test_absolute_duration(self):
        start=parse_local("2026-10-25T01:30","Europe/Berlin"); end=absolute_end(start,90,start.tzinfo)
        self.assertEqual(end.isoformat(timespec="minutes"),"2026-10-25T02:00+01:00")

if __name__=="__main__": unittest.main()
