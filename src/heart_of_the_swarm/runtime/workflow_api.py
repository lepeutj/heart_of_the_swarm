from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.runtime.api import create_app
from heart_of_the_swarm.runtime.workflow import StandaloneWorkflowRuntime

settings = Settings()
app = create_app(
    lambda: StandaloneWorkflowRuntime(settings),
    title="Heart of the Swarm Workflow Runtime",
    http_policy=settings.http_policy(),
)
