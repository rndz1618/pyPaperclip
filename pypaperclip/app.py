from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from .adapters import AgentAdapter, EchoAdapter, HttpAdapter, OpenAICompatibleAdapter, SubprocessAdapter
from .migrations import MigrationRunner
from .models import AgentIn, CompanyIn, GoalIn, TaskIn
from .models import ApiKeyIn
from .security import can, hash_key, new_api_key, redact
from .services import WorkerController


DB_PATH = os.getenv("PYPAPERCLIP_DB", "pypaperclip.db")

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def lease_until(seconds: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def require_entity(table: str, entity_id: str, label: str) -> dict[str, Any]:
    item = store.one(f"SELECT * FROM {table} WHERE id=?", (entity_id,))
    if not item:
        raise HTTPException(404, f"{label} not found")
    return item

def safe_agent(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if result.get("config_json"):
        try:
            result["config_json"] = json.dumps(redact(json.loads(result["config_json"])))
        except (TypeError, json.JSONDecodeError):
            result["config_json"] = "{}"
    return result


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {
            "echo": EchoAdapter(),
            "subprocess": SubprocessAdapter(),
            "http": HttpAdapter(),
            "openai": OpenAICompatibleAdapter(),
        }

    def register(self, name: str, adapter: AgentAdapter) -> None:
        self._adapters[name] = adapter

    def get(self, name: str) -> AgentAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise ValueError(f"Unknown adapter: {name}") from exc


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, budget_cents INTEGER NOT NULL DEFAULT 0,
                spent_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                name TEXT NOT NULL, adapter TEXT NOT NULL DEFAULT 'echo', role TEXT NOT NULL DEFAULT 'worker',
                budget_cents INTEGER NOT NULL DEFAULT 0, spent_cents INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL, agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
                title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 100, result_json TEXT, idempotency_key TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                lease_id TEXT, lease_expires_at TEXT, available_at TEXT, last_error TEXT,
                approval_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, event TEXT NOT NULL,
                entity_type TEXT, entity_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(status, priority, created_at);
            """)
            MigrationRunner(self.conn).run()

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def many(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def audit(self, company_id: str | None, event: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO audit_log(company_id,event,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                          (company_id, event, entity_type, entity_id, json.dumps(redact(payload)), now()))
        self.conn.commit()

    def recover_expired_tasks(self) -> int:
        events: list[tuple[str, str, dict[str, Any]]] = []
        with self.lock, self.conn:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (now(),),
            ).fetchall()
            for row in rows:
                task = dict(row)
                if task["attempt_count"] >= task["max_attempts"]:
                    self.conn.execute(
                        "UPDATE tasks SET status='failed', last_error=?, result_json=?, lease_id=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                        ("Lease expired after maximum attempts", json.dumps({"error": "Lease expired after maximum attempts"}), now(), task["id"]),
                    )
                    self.move_to_dead_letter(task, "lease_expired_max_attempts")
                    events.append((task["company_id"], task["id"], "task.failed.max_attempts", {"attempt_count": task["attempt_count"]}))
                else:
                    self.conn.execute(
                        "UPDATE tasks SET status='queued', lease_id=NULL, lease_expires_at=NULL, available_at=NULL, updated_at=? WHERE id=?",
                        (now(), task["id"]),
                    )
                    events.append((task["company_id"], task["id"], "task.requeued.lease_expired", {"attempt_count": task["attempt_count"]}))
        for company_id, task_id, event, payload in events:
            self.audit(company_id, event, "task", task_id, payload)
        return len(events)

    def claim_task(self) -> dict[str, Any] | None:
        self.recover_expired_tasks()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT t.* FROM tasks t LEFT JOIN agents a ON a.id=t.agent_id WHERE t.status='queued' AND (t.available_at IS NULL OR t.available_at <= ?) AND (t.agent_id IS NULL OR a.status='active') ORDER BY t.priority ASC, t.created_at ASC LIMIT 1", (now(),)).fetchone()
            if not row:
                return None
            task = dict(row)
            task["attempt_count"] += 1
            task["lease_id"] = new_id("lease")
            self.conn.execute(
                "UPDATE tasks SET status='running', attempt_count=attempt_count+1, lease_id=?, lease_expires_at=?, updated_at=? WHERE id=? AND status='queued'",
                (task["lease_id"], lease_until(int(os.getenv("PYPAPERCLIP_LEASE_SECONDS", "30"))), now(), task["id"]),
            )
            return task

    def close(self) -> None:
        self.conn.close()

    def move_to_dead_letter(self, task: dict[str, Any], reason: str) -> None:
        self.conn.execute(
            "INSERT INTO dead_letter_tasks(task_id,company_id,reason,payload_json,created_at) VALUES(?,?,?,?,?)",
            (task["id"], task["company_id"], reason, json.dumps(task), now()),
        )
        self.conn.commit()


store = Store(DB_PATH)
adapters = AdapterRegistry()


def execute_one() -> dict[str, Any] | None:
    task = store.claim_task()
    if not task:
        return None
    agent = store.one("SELECT * FROM agents WHERE id=? AND enabled=1 AND status='active'", (task["agent_id"],)) if task["agent_id"] else None
    if not agent:
        result = {"error": "No enabled agent assigned"}
        with store.conn:
            store.conn.execute("UPDATE tasks SET status='failed', result_json=?, lease_id=NULL, lease_expires_at=NULL, available_at=NULL, updated_at=? WHERE id=? AND lease_id=?", (json.dumps(result), now(), task["id"], task["lease_id"]))
        store.move_to_dead_letter(task, "no_enabled_agent")
        store.audit(task["company_id"], "task.failed", "task", task["id"], result)
        return result
    company = store.one("SELECT * FROM companies WHERE id=?", (task["company_id"],))
    if company and company["budget_cents"] and company["spent_cents"] >= company["budget_cents"]:
        result = {"error": "Company budget exceeded"}
        with store.conn:
            store.conn.execute("UPDATE tasks SET status='blocked', result_json=?, lease_id=NULL, lease_expires_at=NULL, available_at=NULL, updated_at=? WHERE id=? AND lease_id=?", (json.dumps(result), now(), task["id"], task["lease_id"]))
        store.audit(task["company_id"], "task.blocked.budget", "task", task["id"], result)
        return result
    try:
        result = adapters.get(agent["adapter"]).run(task, agent)
        safe_result = redact(result)
        cost = 1
        with store.conn:
            store.conn.execute("UPDATE tasks SET status='done', result_json=?, lease_id=NULL, lease_expires_at=NULL, available_at=NULL, last_error=NULL, updated_at=? WHERE id=? AND lease_id=?", (json.dumps(safe_result), now(), task["id"], task["lease_id"]))
            store.conn.execute("UPDATE agents SET spent_cents=spent_cents+? WHERE id=?", (cost, agent["id"]))
            store.conn.execute("UPDATE companies SET spent_cents=spent_cents+? WHERE id=?", (cost, task["company_id"]))
        store.audit(task["company_id"], "task.completed", "task", task["id"], {"agent_id": agent["id"], "cost_cents": cost})
        return safe_result
    except Exception as exc:
        result = {"error": str(exc)}
        retryable = task["attempt_count"] < task["max_attempts"]
        next_status = "queued" if retryable else "failed"
        backoff_seconds = min(60, 2 ** max(0, task["attempt_count"] - 1)) if retryable else 0
        with store.conn:
            store.conn.execute(
                "UPDATE tasks SET status=?, result_json=?, last_error=?, lease_id=NULL, lease_expires_at=NULL, available_at=?, updated_at=? WHERE id=? AND lease_id=?",
                (next_status, json.dumps(result), str(exc), lease_until(backoff_seconds) if retryable else None, now(), task["id"], task["lease_id"]),
            )
        if not retryable:
            store.move_to_dead_letter(task, "max_attempts_exceeded")
        store.audit(task["company_id"], "task.retry_scheduled" if retryable else "task.failed", "task", task["id"], {**result, "attempt_count": task["attempt_count"], "max_attempts": task["max_attempts"], "backoff_seconds": backoff_seconds})
        return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = WorkerController(execute_one)
    await worker.start()
    yield
    await worker.drain(float(os.getenv("PYPAPERCLIP_DRAIN_TIMEOUT", "60")))
    store.close()


app = FastAPI(title="pyPaperclip", version="0.4.0", lifespan=lifespan)

def auth_required() -> bool:
    return os.getenv("PYPAPERCLIP_AUTH_REQUIRED", "1") == "1"

def principal_from_request(request: Request) -> dict[str, Any]:
    token = request.headers.get("x-api-key", "")
    authorization = request.headers.get("authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    bootstrap = os.getenv("PYPAPERCLIP_BOOTSTRAP_TOKEN", "")
    if bootstrap and token == bootstrap:
        return {"id": "bootstrap", "role": "owner", "company_id": None}
    if not token:
        raise HTTPException(401, "API key required")
    key = store.one("SELECT id, role, company_id FROM api_keys WHERE key_hash=? AND enabled=1", (hash_key(token),))
    if not key:
        raise HTTPException(401, "Invalid or disabled API key")
    with store.conn:
        store.conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now(), key["id"]))
    return key

def target_company_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "companies":
        return parts[1]
    if len(parts) >= 2 and parts[0] in {"tasks", "agents"}:
        table = "tasks" if parts[0] == "tasks" else "agents"
        item = store.one(f"SELECT company_id FROM {table} WHERE id=?", (parts[1],))
        return item["company_id"] if item else None
    return None

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if not auth_required() or request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)
    try:
        principal = principal_from_request(request)
        request.state.principal = principal
        path = request.url.path
        if path == "/api-keys":
            action = "admin"
        elif path.endswith("/approve"):
            action = "approve"
        elif any(path.endswith("/" + action) for action in ("pause", "resume", "terminate")):
            action = "lifecycle"
        else:
            action = "read" if request.method in {"GET", "HEAD"} else "write"
        if not can(principal["role"], action):
            raise HTTPException(403, "Insufficient role permissions")
        company_id = target_company_id(path)
        if principal.get("company_id") and company_id and principal["company_id"] != company_id:
            raise HTTPException(403, "API key is outside this company scope")
    except HTTPException as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "pyPaperclip"}

@app.post("/companies")
def create_company(body: CompanyIn) -> dict[str, Any]:
    item = {"id": new_id("co"), "name": body.name, "budget_cents": body.budget_cents, "created_at": now()}
    with store.conn:
        store.conn.execute("INSERT INTO companies(id,name,budget_cents,created_at) VALUES(?,?,?,?)", tuple(item.values()))
    store.audit(item["id"], "company.created", "company", item["id"], item)
    return item

@app.get("/companies")
def list_companies(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    total = store.one("SELECT COUNT(*) AS count FROM companies")["count"]
    items = store.many("SELECT * FROM companies ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"items": items, "limit": limit, "offset": offset, "total": total}

@app.get("/companies/{company_id}")
def get_company(company_id: str) -> dict[str, Any]:
    return require_entity("companies", company_id, "Company")

@app.post("/companies/{company_id}/goals")
def create_goal(company_id: str, body: GoalIn) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    item = {"id": new_id("goal"), "company_id": company_id, "title": body.title, "status": "active", "created_at": now()}
    with store.conn:
        store.conn.execute("INSERT INTO goals(id,company_id,title,status,created_at) VALUES(?,?,?,?,?)", tuple(item.values()))
    store.audit(company_id, "goal.created", "goal", item["id"], item)
    return item

@app.post("/companies/{company_id}/agents")
def create_agent(company_id: str, body: AgentIn) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    try: adapters.get(body.adapter)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    item = {"id": new_id("agent"), "company_id": company_id, "name": body.name, "adapter": body.adapter, "role": body.role, "budget_cents": body.budget_cents, "config_json": json.dumps(body.config), "created_at": now()}
    with store.conn:
        store.conn.execute("INSERT INTO agents(id,company_id,name,adapter,role,budget_cents,config_json,created_at) VALUES(?,?,?,?,?,?,?,?)", tuple(item.values()))
    store.audit(company_id, "agent.created", "agent", item["id"], item)
    return item

@app.get("/companies/{company_id}/agents")
def list_agents(company_id: str, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    total = store.one("SELECT COUNT(*) AS count FROM agents WHERE company_id=?", (company_id,))["count"]
    items = [safe_agent(item) for item in store.many("SELECT * FROM agents WHERE company_id=? ORDER BY created_at LIMIT ? OFFSET ?", (company_id, limit, offset))]
    return {"items": items, "limit": limit, "offset": offset, "total": total}

@app.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict[str, Any]:
    return safe_agent(require_entity("agents", agent_id, "Agent"))

def actor(request: Request) -> str:
    return getattr(request.state, "principal", {"id": "local"}).get("id", "local")

@app.post("/api-keys")
def create_api_key(body: ApiKeyIn, request: Request) -> dict[str, Any]:
    if body.role not in {"owner", "admin", "operator", "viewer"}:
        raise HTTPException(422, "Invalid API key role")
    if body.company_id:
        require_entity("companies", body.company_id, "Company")
    principal = getattr(request.state, "principal", {"company_id": None})
    if principal.get("company_id") and principal["company_id"] != body.company_id:
        raise HTTPException(403, "API key is outside this company scope")
    token = new_api_key()
    item = {"id": new_id("key"), "name": body.name, "role": body.role, "company_id": body.company_id, "created_at": now()}
    with store.conn:
        store.conn.execute("INSERT INTO api_keys(id,name,key_hash,role,company_id,created_at) VALUES(?,?,?,?,?,?)", (item["id"], item["name"], hash_key(token), item["role"], item["company_id"], item["created_at"]))
    store.audit(body.company_id, "api_key.created", "api_key", item["id"], {**item, "api_key": token})
    return {**item, "api_key": token, "warning": "Store this key now; it will not be shown again."}

@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str, request: Request) -> dict[str, Any]:
    task = require_entity("tasks", task_id, "Task")
    if task["status"] != "pending_approval":
        raise HTTPException(409, "Task is not awaiting approval")
    with store.conn:
        store.conn.execute("UPDATE tasks SET status='queued', updated_at=? WHERE id=?", (now(), task_id))
        store.conn.execute("UPDATE task_approvals SET approved_by=?, approved_at=? WHERE task_id=?", (actor(request), now(), task_id))
    store.audit(task["company_id"], "task.approved", "task", task_id, {"approved_by": actor(request)})
    return store.one("SELECT * FROM tasks WHERE id=?", (task_id,)) or {}

@app.post("/agents/{agent_id}/{action}")
def agent_lifecycle(agent_id: str, action: str, request: Request) -> dict[str, Any]:
    if action not in {"pause", "resume", "terminate"}:
        raise HTTPException(404, "Unsupported agent lifecycle action")
    agent = require_entity("agents", agent_id, "Agent")
    status = {"pause": "paused", "resume": "active", "terminate": "terminated"}[action]
    enabled = 0 if action == "terminate" else 1
    with store.conn:
        store.conn.execute("UPDATE agents SET status=?, enabled=? WHERE id=?", (status, enabled, agent_id))
    store.audit(agent["company_id"], f"agent.{action}", "agent", agent_id, {"actor": actor(request), "status": status})
    return store.one("SELECT * FROM agents WHERE id=?", (agent_id,)) or {}

@app.post("/companies/{company_id}/tasks")
def create_task(company_id: str, body: TaskIn) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    if body.goal_id:
        goal = require_entity("goals", body.goal_id, "Goal")
        if goal["company_id"] != company_id:
            raise HTTPException(422, "Goal belongs to another company")
    if body.agent_id:
        agent = require_entity("agents", body.agent_id, "Agent")
        if agent["company_id"] != company_id:
            raise HTTPException(422, "Agent belongs to another company")
    if body.idempotency_key:
        existing = store.one("SELECT * FROM tasks WHERE company_id=? AND idempotency_key=?", (company_id, body.idempotency_key))
        if existing:
            return {**existing, "idempotent_replay": True}
    status = "pending_approval" if body.approval_required else "queued"
    item = {"id": new_id("task"), "company_id": company_id, "goal_id": body.goal_id, "agent_id": body.agent_id, "title": body.title, "description": body.description, "priority": body.priority, "idempotency_key": body.idempotency_key, "max_attempts": body.max_attempts, "approval_required": int(body.approval_required), "created_at": now(), "updated_at": now()}
    with store.conn:
        store.conn.execute("INSERT INTO tasks(id,company_id,goal_id,agent_id,title,description,priority,idempotency_key,max_attempts,approval_required,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (item["id"], item["company_id"], item["goal_id"], item["agent_id"], item["title"], item["description"], item["priority"], item["idempotency_key"], item["max_attempts"], item["approval_required"], status, item["created_at"], item["updated_at"]))
        if body.approval_required:
            store.conn.execute("INSERT INTO task_approvals(task_id,required) VALUES(?,1)", (item["id"],))
    store.audit(company_id, "task.pending_approval" if body.approval_required else "task.queued", "task", item["id"], item)
    return {**item, "status": status}

@app.get("/companies/{company_id}/tasks")
def list_tasks(company_id: str, status: str | None = None, goal_id: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    filters = ["company_id=?"]
    params: list[Any] = [company_id]
    if status:
        filters.append("status=?")
        params.append(status)
    if goal_id:
        filters.append("goal_id=?")
        params.append(goal_id)
    where = " AND ".join(filters)
    total = store.one(f"SELECT COUNT(*) AS count FROM tasks WHERE {where}", tuple(params))["count"]
    items = store.many(f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", tuple(params + [limit, offset]))
    return {"items": items, "limit": limit, "offset": offset, "total": total}

@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    return require_entity("tasks", task_id, "Task")

@app.get("/companies/{company_id}/dead-letter")
def list_dead_letter(company_id: str, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    require_entity("companies", company_id, "Company")
    total = store.one("SELECT COUNT(*) AS count FROM dead_letter_tasks WHERE company_id=?", (company_id,))["count"]
    items = store.many("SELECT * FROM dead_letter_tasks WHERE company_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (company_id, limit, offset))
    return {"items": items, "limit": limit, "offset": offset, "total": total}

@app.get("/audit")
def audit(company_id: str | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    if company_id:
        require_entity("companies", company_id, "Company")
        total = store.one("SELECT COUNT(*) AS count FROM audit_log WHERE company_id=?", (company_id,))["count"]
        items = store.many("SELECT * FROM audit_log WHERE company_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (company_id, limit, offset))
    else:
        total = store.one("SELECT COUNT(*) AS count FROM audit_log")["count"]
        items = store.many("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"items": items, "limit": limit, "offset": offset, "total": total}
