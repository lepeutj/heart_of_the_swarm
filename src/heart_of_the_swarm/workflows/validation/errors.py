from pydantic import BaseModel, ConfigDict


class WorkflowValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    node_id: str | None = None
    field: str | None = None


class WorkflowValidationError(ValueError):
    def __init__(self, issues: list[WorkflowValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))
