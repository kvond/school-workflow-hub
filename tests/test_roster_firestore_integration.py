"""Real state serialization/path gates with synthetic native roster fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from school_workflow_hub.adapters import roster_bootstrap as rb
from school_workflow_hub.adapters.firestore_state import FirestoreState
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
from tests import test_roster_bootstrap as bootstrap_fixtures, test_roster_worker as worker_fixtures


class NativeStateTransport:
    """Disposable Firestore wire-format/CAS model, not a live capability."""
    def __init__(self):
        self.rows = {}
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.calls = []

    def __call__(self, operation, body):
        self.calls.append((operation, deepcopy(body)))
        stamp = self.now.isoformat()
        if operation == "batchGet":
            rows = []
            for name in body["documents"]:
                if name not in self.rows:
                    rows.append({"missing": name, "readTime": stamp})
                else:
                    value, version = self.rows[name]
                    rows.append({"found": {"name": name, "updateTime": version,
                        "fields": {"payload": {"stringValue": json.dumps(value)}}}, "readTime": stamp})
            return rows
        assert operation == "commit"
        for write in body["writes"]:
            prior = self.rows.get(write["update"]["name"])
            condition = write["currentDocument"]
            if ((condition == {"exists": False} and prior is not None)
                    or ("updateTime" in condition and (prior is None or prior[1] != condition["updateTime"]))):
                raise Conflict("state_version_conflict")
        self.now += timedelta(microseconds=1)
        stamp = self.now.isoformat()
        for write in body["writes"]:
            self.rows[write["update"]["name"]] = (
                json.loads(write["update"]["fields"]["payload"]["stringValue"]), stamp)
        return {"commitTime": stamp, "writeResults": [{"updateTime": stamp} for _ in body["writes"]]}


class RosterFirestoreIntegrationTests(unittest.TestCase):
    def state(self):
        wire = NativeStateTransport()
        return FirestoreState("fictional-hub", token_provider=lambda: "synthetic", transport=wire), wire

    def bootstrap(self):
        fixture = bootstrap_fixtures.BootstrapTests(); fixture.setUp()
        fixture.port, wire = self.state()
        fixture.worker = rb.BootstrapWorker(fixture.port, fixture.reader, fixture.mirror,
            read_config=lambda: deepcopy(fixture.config), targets=fixture.targets,
            titles=fixture.titles, before_write=fixture.guard)
        return fixture, wire

    def ordinary(self):
        fixture = worker_fixtures.WorkerTests(); fixture.setUp()
        fixture.port, wire = self.state()
        return fixture, wire

    def test_bootstrap_writes_and_reads_existing_exact_intent_keys_through_real_adapter(self):
        f, wire = self.bootstrap()
        proposal = f.worker.prepare()
        result = f.apply(proposal)
        self.assertEqual(result["status"], "verified_not_activated")
        self.assertEqual(f.mirror.writes, 1)
        for rid in (f.worker.intent_key, f.worker.sync_intent_key):
            self.assertIn(f.port.prefix + rid, wire.rows)
            self.assertEqual(f.port.read([rid]).documents[rid].value["state"], "verified")
        self.assertEqual(f.worker.reconcile(), result)
        self.assertEqual(f.mirror.writes, 1)

    def test_ordinary_worker_uses_same_pending_namespace_and_verified_readback(self):
        f, wire = self.ordinary()
        result = f.run_worker()
        self.assertEqual(result["status"], "verified")
        rid = f.worker().intent_key
        self.assertIn(f.port.prefix + rid, wire.rows)
        self.assertEqual(f.port.read([rid]).documents[rid].value["state"], "verified")
        self.assertEqual(f.run_worker(), result)
        self.assertEqual(f.mirror.writes, 1)

    def test_uncertain_bootstrap_barrier_blocks_ordinary_and_repeated_native_writes(self):
        f, wire = self.bootstrap()
        proposal = f.worker.prepare()
        f.mirror.lose_response = True
        with self.assertRaisesRegex(ConnectionError, "uncertain_native_write"):
            f.apply(proposal)
        ordinary, _ = self.ordinary(); ordinary.port = f.port
        with self.assertRaisesRegex(ConnectionError, "uncertain_write_requires_readback"):
            ordinary.run_worker()
        with self.assertRaisesRegex(ConnectionError, "existing_intent"):
            f.apply(proposal)
        self.assertEqual(ordinary.mirror.writes, 0)
        self.assertEqual(f.mirror.writes, 1)
        self.assertEqual(f.worker.reconcile()["status"], "verified_not_activated")
        self.assertEqual(f.mirror.writes, 1)

    def test_lost_pending_intent_response_cannot_reach_native_writer(self):
        f, wire = self.bootstrap()
        proposal = f.worker.prepare()
        def lost(operation, body):
            result = wire(operation, body)
            if operation == "commit" and any(w["update"]["name"] == f.port.prefix + f.worker.intent_key
                    for w in body["writes"]):
                raise StoreUnavailable("synthetic_lost_response")
            return result
        f.port.transport = lost
        with self.assertRaises(StoreUnavailable): f.apply(proposal)
        with self.assertRaisesRegex(ConnectionError, "existing_intent"): f.apply(proposal)
        self.assertEqual(f.mirror.writes, 0)
        self.assertEqual(f.port.read([f.worker.sync_intent_key]).documents[f.worker.sync_intent_key].value["state"], "pending")
