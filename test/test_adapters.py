import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from pypaperclip.adapters import HttpAdapter, OpenAICompatibleAdapter, SubprocessAdapter


class JsonHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self.server.received = json.loads(body)
        response = json.dumps({"ok": True, "received_title": self.server.received["task"]["title"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_):
        return


class PyPaperclipAdapterTest(unittest.TestCase):
    def test_subprocess_adapter_is_explicit_and_non_shell(self):
        previous = os.environ.get("PYPAPERCLIP_ALLOW_SUBPROCESS")
        os.environ["PYPAPERCLIP_ALLOW_SUBPROCESS"] = "1"
        try:
            result = SubprocessAdapter().run(
                {"title": "local", "description": "hello"},
                {"config_json": json.dumps({"command": ["python3", "-c", "print('ok')"]})},
            )
            self.assertEqual(result["returncode"], 0)
            self.assertIn("ok", result["output"])
        finally:
            if previous is None:
                os.environ.pop("PYPAPERCLIP_ALLOW_SUBPROCESS", None)
            else:
                os.environ["PYPAPERCLIP_ALLOW_SUBPROCESS"] = previous

    def test_http_adapter_posts_json_to_local_agent(self):
        server = HTTPServer(("127.0.0.1", 0), JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = HttpAdapter().run(
                {"id": "task_1", "title": "web", "description": "hello"},
                {"id": "agent_1", "name": "local", "role": "worker", "config_json": json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "timeout_seconds": 2})},
            )
            self.assertEqual(result["response"]["received_title"], "web")
            self.assertEqual(server.received["task"]["description"], "hello")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_openai_adapter_requires_key_before_network(self):
        os.environ.pop("PYPAPERCLIP_TEST_KEY", None)
        with self.assertRaisesRegex(RuntimeError, "missing API key"):
            OpenAICompatibleAdapter().run(
                {"title": "model", "description": "hello"},
                {"config_json": json.dumps({"base_url": "http://127.0.0.1:1/v1", "model": "test", "api_key_env": "PYPAPERCLIP_TEST_KEY"})},
            )


if __name__ == "__main__":
    unittest.main()
