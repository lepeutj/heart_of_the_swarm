from sqlalchemy import select

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    DevelopmentPolicySource,
)
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.models import (
    AgentRecord,
    AgentVersionRecord,
    MCPServerRecord,
    RunRecord,
    WorkflowRecord,
    WorkflowRunRecord,
    WorkflowVersionRecord,
)

COLLECTION_POLICIES = (
    (AuthorizationAction.CAPABILITY_READ, "capability:catalog"),
    (AuthorizationAction.CAPABILITY_MANAGE, "capability:catalog"),
    (AuthorizationAction.WORKFLOW_READ, "workflow:catalog"),
    (AuthorizationAction.WORKFLOW_READ, "workflow:triggers"),
    (AuthorizationAction.WORKFLOW_READ, "workflow:interruptions"),
    (AuthorizationAction.WORKFLOW_EDIT, "workflow:collection"),
    (AuthorizationAction.WORKFLOW_EDIT, "workflow:validation"),
    (AuthorizationAction.AGENT_READ, "agent:catalog"),
    (AuthorizationAction.AGENT_EDIT, "agent:collection"),
    (AuthorizationAction.AGENT_EDIT, "agent:builder"),
    (AuthorizationAction.AGENT_EDIT, "agent:validation"),
    (AuthorizationAction.TRACE_READ, "trace:usage"),
)


async def bootstrap_development_policies(
    source: DevelopmentPolicySource,
    database: Database,
) -> None:
    """Reconstruct exact local policies from persisted product identities."""
    for action, resource in COLLECTION_POLICIES:
        source.grant(action, resource)

    async with database.session() as session:
        workflows = (await session.execute(select(WorkflowRecord.id))).scalars().all()
        workflow_versions = (
            (await session.execute(select(WorkflowVersionRecord.id))).scalars().all()
        )
        agents = (await session.execute(select(AgentRecord.id))).scalars().all()
        agent_versions = (await session.execute(select(AgentVersionRecord.id))).scalars().all()
        mcp_servers = (await session.execute(select(MCPServerRecord.id))).scalars().all()
        workflow_traces = (
            (await session.execute(select(WorkflowRunRecord.trace_id))).scalars().all()
        )
        agent_traces = (await session.execute(select(RunRecord.trace_id))).scalars().all()

    for workflow_id in workflows:
        grant_workflow(source, workflow_id)
    for version_id in workflow_versions:
        grant_workflow_version(source, version_id)
    for agent_id in agents:
        grant_agent(source, agent_id)
    for version_id in agent_versions:
        grant_agent_version(source, version_id)
    for server_id in mcp_servers:
        source.grant(AuthorizationAction.CAPABILITY_MANAGE, f"capability:mcp:{server_id}")
    for trace_id in (*workflow_traces, *agent_traces):
        grant_trace(source, trace_id)


def grant_workflow(source: DevelopmentPolicySource, workflow_id: object) -> None:
    resource = f"workflow:{workflow_id}"
    source.grant(AuthorizationAction.WORKFLOW_READ, resource)
    source.grant(AuthorizationAction.WORKFLOW_EDIT, resource)


def grant_workflow_version(source: DevelopmentPolicySource, version_id: object) -> None:
    resource = f"workflow_version:{version_id}"
    source.grant(AuthorizationAction.WORKFLOW_READ, resource)
    source.grant(AuthorizationAction.WORKFLOW_EDIT, resource)
    source.grant(AuthorizationAction.WORKFLOW_EXECUTE, resource)


def grant_agent(source: DevelopmentPolicySource, agent_id: object) -> None:
    resource = f"agent:{agent_id}"
    source.grant(AuthorizationAction.AGENT_READ, resource)
    source.grant(AuthorizationAction.AGENT_EDIT, resource)


def grant_agent_version(source: DevelopmentPolicySource, version_id: object) -> None:
    resource = f"agent_version:{version_id}"
    source.grant(AuthorizationAction.AGENT_READ, resource)
    source.grant(AuthorizationAction.AGENT_EDIT, resource)
    source.grant(AuthorizationAction.AGENT_EXECUTE, resource)


def grant_trace(source: DevelopmentPolicySource, trace_id: object) -> None:
    source.grant(AuthorizationAction.TRACE_READ, f"trace:{trace_id}")
