import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class PyPaperclipDomainTest(unittest.TestCase):
    def test_validation_pagination_details_and_dead_letter(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["PYPAPERCLIP_DB"] = os.path.join(directory, "domain.db")
            os.environ["PYPAPERCLIP_AUTH_REQUIRED"] = "0"
            import pypaperclip.app as module
            module = importlib.reload(module)
            with TestClient(module.app) as client:
                first = client.post("/companies", json={"name": "One"}).json()
                second = client.post("/companies", json={"name": "Two"}).json()
                goal = client.post(f"/companies/{first['id']}/goals", json={"title": "Goal"}).json()
                agent = client.post(f"/companies/{first['id']}/agents", json={"name": "Worker"}).json()

                invalid = client.post(
                    f"/companies/{second['id']}/tasks",
                    json={"title": "Invalid", "goal_id": goal["id"], "agent_id": agent["id"]},
                )
                self.assertEqual(invalid.status_code, 422)

                task = client.post(f"/companies/{first['id']}/tasks", json={"title": "Valid", "agent_id": agent["id"]}).json()
                detail = client.get(f"/tasks/{task['id']}").json()
                self.assertEqual(detail["id"], task["id"])
                page = client.get(f"/companies/{first['id']}/tasks?limit=1&offset=0").json()
                self.assertEqual(page["total"], 1)
                self.assertEqual(len(page["items"]), 1)

                no_agent = client.post(f"/companies/{first['id']}/tasks", json={"title": "Dead letter", "max_attempts": 1}).json()
                module.execute_one()
                module.execute_one()
                dead = client.get(f"/companies/{first['id']}/dead-letter").json()
                self.assertEqual(dead["total"], 1)
                self.assertEqual(dead["items"][0]["task_id"], no_agent["id"])
                self.assertEqual(client.get(f"/companies/{first['id']}").json()["name"], "One")
                versions = module.store.many("SELECT version FROM schema_migrations ORDER BY version")
                self.assertEqual([row["version"] for row in versions], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
