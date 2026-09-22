from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Protocol


class AgentAdapter(Protocol):
    def run(self, task: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]: ...


def config_of(agent: dict[str, Any]) -> dict[str, Any]:
    raw = agent.get("config_json") or "{}"
    try:
        config = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise RuntimeError("Agent config_json is invalid JSON") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Agent configuration must be a JSON object")
    return config


def timeout_of(config: dict[str, Any], default: float = 30.0) -> float:
    try:
        timeout = float(config.get("timeout_seconds", default))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("timeout_seconds must be numeric") from exc
    if timeout <= 0 or timeout > 300:
        raise RuntimeError("timeout_seconds must be between 0 and 300")
    return timeout


class EchoAdapter:
    def run(self, task: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        return {"summary": f"Echo adapter completed: {task['title']}", "output": task.get("description", "")}


class SubprocessAdapter:
    """Run an argv list without a shell; disabled unless explicitly enabled."""

    def run(self, task: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        if os.getenv("PYPAPERCLIP_ALLOW_SUBPROCESS", "0") != "1":
            raise RuntimeError("subprocess adapter is disabled; set PYPAPERCLIP_ALLOW_SUBPROCESS=1")
        config = config_of(agent)
        command = config.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise RuntimeError("subprocess adapter requires config.command as a non-empty string list")
        cwd = config.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise RuntimeError("subprocess config.cwd must be a string")
        try:
            completed = subprocess.run(
                command,
                input=task.get("description", ""),
                capture_output=True,
                text=True,
                timeout=timeout_of(config),
                cwd=cwd,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("subprocess adapter timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"subprocess exited with code {completed.returncode}: {completed.stderr[-500:]}")
        return {"summary": f"Subprocess completed: {task['title']}", "output": completed.stdout[-10000:], "returncode": completed.returncode}


class HttpAdapter:
    """POST a task payload to an HTTP agent endpoint with a bounded timeout."""

    def run(self, task: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        config = config_of(agent)
        url = config.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise RuntimeError("http adapter requires config.url with http:// or https://")
        headers = config.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise RuntimeError("http config.headers must be a string map")
        payload = json.dumps({"task": task, "agent": {"id": agent.get("id"), "name": agent.get("name"), "role": agent.get("role")}}).encode()
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_of(config)) as response:
                raw = response.read(100000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"http adapter request failed: {exc}") from exc
        try:
            body: Any = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return {"summary": f"HTTP agent completed: {task['title']}", "response": body}


class OpenAICompatibleAdapter:
    """Call an OpenAI-compatible /chat/completions endpoint using stdlib HTTP."""

    def run(self, task: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        config = config_of(agent)
        base_url = config.get("base_url", "https://api.openai.com/v1")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise RuntimeError("openai adapter requires a valid config.base_url")
        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        api_key_env = config.get("api_key_env", "OPENAI_API_KEY")
        if not isinstance(api_key_env, str) or not api_key_env:
            raise RuntimeError("openai config.api_key_env must be a string")
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API key environment variable: {api_key_env}")
        model = config.get("model")
        if not isinstance(model, str) or not model:
            raise RuntimeError("openai adapter requires config.model")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": f"{task['title']}\n\n{task.get('description', '')}"}],
        }).encode()
        request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_of(config)) as response:
                raw = response.read(200000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"openai-compatible request failed: {exc}") from exc
        try:
            body = json.loads(raw)
            message = body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("openai-compatible response did not contain choices[0].message.content") from exc
        return {"summary": f"OpenAI-compatible agent completed: {task['title']}", "output": message, "usage": body.get("usage")}
