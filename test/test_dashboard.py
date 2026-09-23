import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class PyPaperclipDashboardTest(unittest.TestCase):
    def test_dashboard_public_metrics_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["PYPAPERCLIP_DB"] = os.path.join(directory, "dashboard.db")
            os.environ["PYPAPERCLIP_AUTH_REQUIRED"] = "1"
            os.environ["PYPAPERCLIP_BOOTSTRAP_TOKEN"] = "dashboard-bootstrap"
            import pypaperclip.app as module
            module = importlib.reload(module)
            try:
                with TestClient(module.app) as client:
                    dashboard = client.get("/dashboard")
                    self.assertEqual(dashboard.status_code, 200)
                    self.assertIn("pyPaperclip / Operator Console", dashboard.text)
                    self.assertEqual(client.get("/metrics").status_code, 401)
                    key = client.post("/api-keys", headers={"x-api-key": "dashboard-bootstrap"}, json={"name": "owner", "role": "owner"}).json()["api_key"]
                    metrics = client.get("/metrics", headers={"x-api-key": key}).json()
                    self.assertEqual(metrics["companies"], 0)
                    self.assertIn("tasks", metrics)
            finally:
                os.environ.pop("PYPAPERCLIP_AUTH_REQUIRED", None)
                os.environ.pop("PYPAPERCLIP_BOOTSTRAP_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
