from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentRecord(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    goal: Mapped[str] = mapped_column(String(500))
    active_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentVersionRecord(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_version: Mapped[str] = mapped_column(String(40))
    system_prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowRecord(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    editor: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowVersionRecord(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    editor: Mapped[dict[str, Any]] = mapped_column(JSON)
    capability_contracts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionThreadRecord(Base):
    __tablename__ = "execution_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MCPServerRecord(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TriggerRecord(Base):
    __tablename__ = "triggers"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('manual', 'webhook', 'schedule')",
            name="ck_triggers_type",
        ),
        CheckConstraint(
            "target_type IN ('agent', 'workflow')",
            name="ck_triggers_target_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    trigger_type: Mapped[str] = mapped_column(String(20), index=True)
    target_type: Mapped[str] = mapped_column(String(20), index=True)
    target_version_id: Mapped[str] = mapped_column(String(36), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("thread_id", "attempt_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(ForeignKey("workflow_versions.id"), index=True)
    thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_threads.id"), nullable=True, index=True
    )
    attempt_index: Mapped[int] = mapped_column(Integer, default=1)
    resumed_from_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True
    )
    resume_checkpoint_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger_id: Mapped[str | None] = mapped_column(
        ForeignKey("triggers.id"), nullable=True, index=True
    )
    trigger_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trigger_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON)
    output: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowRunEventRecord(Base):
    __tablename__ = "workflow_run_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DesignSessionRecord(Base):
    __tablename__ = "design_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(Text)
    builder_provider: Mapped[str] = mapped_column(String(40))
    builder_model_id: Mapped[str] = mapped_column(String(200))
    builder_prompt: Mapped[str] = mapped_column(Text)
    generated_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunEventRecord(Base):
    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TrajectoryStepRecord(Base):
    __tablename__ = "trajectory_steps"
    __table_args__ = (UniqueConstraint("run_id", "attempt", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    component: Mapped[str | None] = mapped_column(String(200), nullable=True)
    langchain_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelUsageRecord(Base):
    __tablename__ = "model_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    resolved_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    success: Mapped[bool] = mapped_column(default=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
