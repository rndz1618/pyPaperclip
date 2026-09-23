from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CompanyIn(BaseModel):
    name: str = Field(min_length=1)
    budget_cents: int = Field(default=0, ge=0)


class GoalIn(BaseModel):
    title: str = Field(min_length=1)


class AgentIn(BaseModel):
    name: str = Field(min_length=1)
    adapter: str = "echo"
    role: str = "worker"
    budget_cents: int = Field(default=0, ge=0)
    config: dict[str, Any] = Field(default_factory=dict)


class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    goal_id: str | None = None
    agent_id: str | None = None
    priority: int = Field(default=100, ge=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=10)
    approval_required: bool = False


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = "viewer"
    company_id: str | None = None
