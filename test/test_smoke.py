import importlib
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class FlakyAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, task, agent):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient failure")
        return {"summary": "recovered", "output": task["title"]}


class PyPaperclipSmokeTest(unittest.TestCase):
    def load_module(self, directory):
        os.environ["PYPAPERCLIP_DB"] = os.path.join(directory, "test.db")
        import pypaperclip.app as module
        return importlib.reload(module)

    def test_create_entities_and_execute_task(self):
        with tempfile.TemporaryDirectory() as d:
            module = self.load_module(d)
            with TestClient(module.app) as client:
                company = client.post("/companies", json={"name": "Test", "budget_cents": 10}).json()
                agent = client.post(f"/companies/{company['id']}/agents", json={"name": "Echo"}).json()
                task = client.post(f"/companies/{company['id']}/tasks", json={"title": "Ping", "agent_id": agent["id"]}).json()
                self.assertEqual(task["status"], "queued")
                result = module.execute_one()
                self.assertEqual(result["summary"], "Echo adapter completed: Ping")
                tasks = client.get(f"/companies/{company['id']}/tasks").json()
                self.assertEqual(tasks["items"][0]["status"], "done")
                audit = client.get(f"/audit?company_id={company['id']}").json()
                self.assertTrue(any(row["event"] == "task.completed" for row in audit["items"]))

    def test_idempotency_retry_and_lease_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            module = self.load_module(d)
            flaky = FlakyAdapter()
            module.adapters.register("flaky", flaky)
            with TestClient(module.app) as client:
                company = client.post("/companies", json={"name": "Reliable", "budget_cents": 10}).json()
                agent = client.post(f"/companies/{company['id']}/agents", json={"name": "Flaky", "adapter": "flaky"}).json()
                payload = {"title": "Retry me", "agent_id": agent["id"], "idempotency_key": "retry-1", "max_attempts": 2}
                first = client.post(f"/companies/{company['id']}/tasks", json=payload).json()
                replay = client.post(f"/companies/{company['id']}/tasks", json=payload).json()
                self.assertEqual(first["id"], replay["id"])
                self.assertTrue(replay["idempotent_replay"])

                module.execute_one()
                retry_state = client.get(f"/companies/{company['id']}/tasks").json()["items"][0]
                self.assertEqual(retry_state["status"], "queued")
                with module.store.conn:
                    module.store.conn.execute("UPDATE tasks SET available_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", first["id"]))
                module.execute_one()
                final_state = client.get(f"/companies/{company['id']}/tasks").json()["items"][0]
                self.assertEqual(final_state["status"], "done")
                self.assertEqual(final_state["attempt_count"], 2)

                stale = client.post(f"/companies/{company['id']}/tasks", json={"title": "Stale", "agent_id": agent["id"]}).json()
                claimed = module.store.claim_task()
                self.assertEqual(claimed["id"], stale["id"])
                with module.store.conn:
                    module.store.conn.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", stale["id"]))
                recovered = module.store.recover_expired_tasks()
                self.assertEqual(recovered, 1)
                recovered_state = client.get(f"/companies/{company['id']}/tasks").json()["items"]
                stale_state = next(row for row in recovered_state if row["id"] == stale["id"])
                self.assertEqual(stale_state["status"], "queued")
                audit = client.get(f"/audit?company_id={company['id']}").json()
                self.assertTrue(any(row["event"] == "task.requeued.lease_expired" for row in audit["items"]))


if __name__ == "__main__":
    unittest.main()
