from pydantic import BaseModel, ConfigDict


class WorkflowCompilationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    node_id: str | None = None


class WorkflowCompilationError(ValueError):
    def __init__(self, issues: list[WorkflowCompilationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in issues))
