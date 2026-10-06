from typing import Literal

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.config import Settings, get_settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.execution import AgentExecutor, RunService
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.mcp_service import MCPServerService
from heart_of_the_swarm.observability import configure_audit_logging
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.service import AgentService
from heart_of_the_swarm.skills import SkillRegistry
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import MCPToolLoader, create_default_registry
from heart_of_the_swarm.triggers.service import TriggerService
from heart_of_the_swarm.triggers.webhook import WebhookTriggerService
from heart_of_the_swarm.validator import AgentSpecValidator
from heart_of_the_swarm.workflow_approvals import WorkflowApprovalService
from heart_of_the_swarm.workflow_execution import WorkflowExecutor, WorkflowRunService
from heart_of_the_swarm.workflow_service import WorkflowService
from heart_of_the_swarm.workflows import WorkflowValidator
from heart_of_the_swarm.workflows.execution.checkpoints import DurableWorkflowCheckpoints


class Application:
    def __init__(
        self, settings: Settings | None = None, process: Literal["api", "worker"] = "api"
    ) -> None:
        self.settings = settings or get_settings()
        log_file = self.settings.api_log_file if process == "api" else self.settings.worker_log_file
        configure_audit_logging(
            log_file,
            self.settings.log_level,
            self.settings.log_max_bytes,
            self.settings.log_backup_count,
        )
        self.tools = create_default_registry(self.settings)
        self.mcp_tools = MCPToolLoader(self.tools, {})
        self.skills = SkillRegistry(self.settings.skills_dir)
        self.providers = ProviderRegistry(self.settings)
        self.validator = AgentSpecValidator(self.tools, self.providers, self.skills)
        self.database = Database(self.settings.database_url)
        self.workflow_checkpoints = DurableWorkflowCheckpoints(self.settings.database_url)
        self.mcp_servers = MCPServerService(self.database)
        self.telemetry = Telemetry(self.settings)
        self.factory = AgentFactory(self.tools, self.skills)
        self.agent_runner = AgentRunner(self.providers, self.validator, self.factory)
        self.workflow_validator = WorkflowValidator(
            lambda: self.tools.names, self.providers.names, lambda: self.skills.names
        )
        self.workflows = WorkflowService(
            self.database, self.workflow_validator, self.validator, self.tools
        )
        self.agents = AgentService(
            self.settings,
            self.tools,
            self.providers,
            self.validator,
            self.database,
            self.telemetry,
            self.skills,
        )
        self.runs = RunService(self.database, self.validator)
        self.triggers = TriggerService(self.database)
        self.workflow_runs = WorkflowRunService(
            self.database,
            self.workflow_validator,
            self.tools,
            self.workflow_checkpoints,
        )
        self.workflow_approvals = WorkflowApprovalService(
            self.database,
            self.workflow_checkpoints,
        )
        self.webhook_triggers = WebhookTriggerService(self.triggers, self.workflow_runs)
        self.executor = AgentExecutor(
            self.settings,
            self.database,
            self.agent_runner,
            self.telemetry,
        )
        self.workflow_executor = WorkflowExecutor(
            self.settings,
            self.database,
            self.workflow_validator,
            self.tools,
            self.agent_runner,
            self.telemetry,
            self.workflow_checkpoints,
        )

    async def initialize(self) -> None:
        await self.database.initialize()
        await self.workflow_checkpoints.start()
        await self.mcp_servers.seed(self.settings.mcp_servers)
        await self.sync_mcp_tools()

    async def sync_mcp_tools(self) -> tuple[str, ...]:
        """Synchronize this process registry with the shared persisted MCP sources."""
        return await self.mcp_tools.sync(await self.mcp_servers.enabled_sources())

    async def close(self) -> None:
        await self.mcp_tools.close()
        await self.workflow_checkpoints.close()
        await self.database.close()
