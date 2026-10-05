from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.runtime.api import create_app
from heart_of_the_swarm.runtime.workflow import StandaloneWorkflowRuntime

app = create_app(
    lambda: StandaloneWorkflowRuntime(Settings()),
    title="Heart of the Swarm Workflow Runtime",
)
