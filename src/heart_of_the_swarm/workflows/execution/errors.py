from uuid import UUID

from pydantic import BaseModel, ConfigDict

from heart_of_the_swarm.workflows.enums import NodeType


class WorkflowExecutionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    workflow_id: UUID
    node_id: str | None = None
    node_type: NodeType | None = None


class WorkflowExecutionError(ValueError):
    def __init__(self, issue: WorkflowExecutionIssue) -> None:
        self.issue = issue
        super().__init__(issue.message)
