from typing import Literal

from heart_of_the_swarm.agent_runtime import AgentRunner
from heart_of_the_swarm.config import Settings, get_settings
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.execution import AgentExecutor, RunService
from heart_of_the_swarm.factory import AgentFactory
from heart_of_the_swarm.observability import configure_audit_logging
from heart_of_the_swarm.providers import ProviderRegistry
from heart_of_the_swarm.service import AgentService
from heart_of_the_swarm.telemetry import Telemetry
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.validator import AgentSpecValidator
from heart_of_the_swarm.workflow_service import WorkflowService
from heart_of_the_swarm.workflows import WorkflowValidator


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
        self.tools = create_default_registry()
        self.providers = ProviderRegistry(self.settings)
        self.validator = AgentSpecValidator(self.tools, self.providers)
        self.database = Database(self.settings.database_url)
        self.telemetry = Telemetry(self.settings)
        self.factory = AgentFactory(self.tools)
        self.agent_runner = AgentRunner(self.providers, self.validator, self.factory)
        self.workflow_validator = WorkflowValidator(self.tools.names, self.providers.names)
        self.workflows = WorkflowService(self.database, self.workflow_validator, self.validator)
        self.agents = AgentService(
            self.settings,
            self.tools,
            self.providers,
            self.validator,
            self.database,
            self.telemetry,
        )
        self.runs = RunService(self.database, self.validator)
        self.executor = AgentExecutor(
            self.settings,
            self.database,
            self.agent_runner,
            self.telemetry,
        )

    async def initialize(self) -> None:
        await self.database.initialize()

    async def close(self) -> None:
        await self.database.close()
