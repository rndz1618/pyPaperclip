import importlib
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class PyPaperclipSecurityTest(unittest.TestCase):
    def test_auth_rbac_approval_lifecycle_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["PYPAPERCLIP_DB"] = os.path.join(directory, "secure.db")
            os.environ["PYPAPERCLIP_AUTH_REQUIRED"] = "1"
            os.environ["PYPAPERCLIP_BOOTSTRAP_TOKEN"] = "bootstrap-secret"
            import pypaperclip.app as module
            module = importlib.reload(module)
            try:
                with TestClient(module.app) as client:
                    self.assertEqual(client.post("/companies", json={"name": "No auth"}).status_code, 401)
                    owner = client.post("/api-keys", headers={"x-api-key": "bootstrap-secret"}, json={"name": "Owner", "role": "owner"}).json()
                    owner_headers = {"x-api-key": owner["api_key"]}
                    company = client.post("/companies", headers=owner_headers, json={"name": "Secure"}).json()
                    viewer = client.post("/api-keys", headers=owner_headers, json={"name": "Viewer", "role": "viewer", "company_id": company["id"]}).json()
                    viewer_headers = {"x-api-key": viewer["api_key"]}
                    self.assertEqual(client.post("/companies", headers=viewer_headers, json={"name": "Denied"}).status_code, 403)
                    agent = client.post(f"/companies/{company['id']}/agents", headers=owner_headers, json={"name": "Worker", "config": {"api_key": "super-secret"}}).json()
                    detail = client.get(f"/agents/{agent['id']}", headers=viewer_headers).json()
                    self.assertNotIn("super-secret", detail["config_json"])
                    self.assertIn("REDACTED", detail["config_json"])
                    task = client.post(f"/companies/{company['id']}/tasks", headers=owner_headers, json={"title": "Dangerous", "agent_id": agent["id"], "approval_required": True}).json()
                    self.assertEqual(task["status"], "pending_approval")
                    self.assertEqual(client.post(f"/tasks/{task['id']}/approve", headers=viewer_headers).status_code, 403)
                    approved = client.post(f"/tasks/{task['id']}/approve", headers=owner_headers).json()
                    self.assertEqual(approved["status"], "queued")
                    paused = client.post(f"/agents/{agent['id']}/pause", headers=owner_headers).json()
                    self.assertEqual(paused["status"], "paused")
                    self.assertIsNone(module.execute_one())
                    resumed = client.post(f"/agents/{agent['id']}/resume", headers=owner_headers).json()
                    self.assertEqual(resumed["status"], "active")
                    module.execute_one()
                    terminated = client.post(f"/agents/{agent['id']}/terminate", headers=owner_headers).json()
                    self.assertEqual(terminated["status"], "terminated")
                    audit = client.get(f"/audit?company_id={company['id']}", headers=owner_headers).json()
                    self.assertTrue(all("super-secret" not in json.dumps(row) for row in audit["items"]))
            finally:
                os.environ.pop("PYPAPERCLIP_AUTH_REQUIRED", None)
                os.environ.pop("PYPAPERCLIP_BOOTSTRAP_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
