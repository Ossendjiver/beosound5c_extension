"""External-lounge listening tests: no real BS5c, network or MA required."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest

file = Path(__file__).resolve().parents[1] / "lib/external_listens.py"
spec = importlib.util.spec_from_file_location("bs5c_external_listens_test", file)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class ExternalListeningTests(unittest.TestCase):
    def event(self):
        t=time.time()
        return {"event_id":"ha-dining-20261121-00000000001",
                "external":True,"observations":2,
                "confirmation_method":"time_separated_audio",
                "room":"dining","source":"AUX",
                "title":"Example Track","artist":"Example Artist",
                "first_observed_at":t-100,"confirmed_at":t-90,
                "ended_at":t-1,"listened_seconds":70,
                "providers":["shazam"]}

    def test_accepts_two_samples(self):
        self.assertEqual(mod.validate_listen(self.event())["observations"],2)

    def test_rejects_single_sample_and_missing_proof(self):
        for patch in ({"observations":1},{"confirmation_method":"single"},
                      {"source":"UNKNOWN"},{"external":False},
                      {"listened_seconds":999999},{"providers":[]}):
            with self.subTest(patch=patch):
                event=self.event()
                event.update(patch)
                with self.assertRaises(ValueError):
                    mod.validate_listen(event)

    def test_duplicate_idempotency_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"db.sqlite3")
            store=mod.ExternalListenStore(path)
            event=self.event()
            self.assertTrue(store.record(event)["accepted"])
            self.assertFalse(store.record(event)["accepted"])
            self.assertEqual(len(mod.ExternalListenStore(path).recent()),1)

    def test_favorite_external_not_assumed_in_ma(self):
        with tempfile.TemporaryDirectory() as folder:
            store=mod.ExternalListenStore(str(Path(folder)/"db.sqlite3"))
            store.record(self.event())
            stored=store.set_favorite({"title":"Example Track","artist":"Example Artist",
                                       "album":"","favorite":True})
            self.assertEqual(stored["scope"],"external")
            item=store.recent()[0]
            self.assertTrue(item["favorite"])
            self.assertIsNone(item["resolved_uri"])

    def test_explicit_uri_resolution_only(self):
        with tempfile.TemporaryDirectory() as folder:
            store=mod.ExternalListenStore(str(Path(folder)/"db.sqlite3"))
            store.record(self.event())
            with self.assertRaises(ValueError):
                store.resolve({"title":"Example Track","artist":"Example Artist","resolved_uri":""})
            store.resolve({"title":"Example Track","artist":"Example Artist",
                          "resolved_uri":"spotify://track/1234567"})
            self.assertEqual(store.recent()[0]["resolved_uri"],"spotify://track/1234567")


if __name__ == "__main__":
    unittest.main()
