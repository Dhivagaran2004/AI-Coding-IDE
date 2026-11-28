from typing import Literal, get_args, get_origin
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    ValidationInfo,
)

from App.schema.ai_schema import CodeAction, SelectedCodeContext, TerminalContext


class AgentPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def reject_blank_text(cls, value, info: ValidationInfo):
        annotation = cls.model_fields[info.field_name].annotation
        if (
            isinstance(value, str)
            and get_origin(annotation) is list
            and get_args(annotation) == (str,)
        ):
            value = [value]
        if info.field_name == "risks" and isinstance(value, list):
            normalized_risks = []
            for item in value:
                if (
                    isinstance(item, dict)
                    and set(item).issubset({"risk", "mitigation"})
                    and isinstance(item.get("risk"), str)
                ):
                    risk = item["risk"].strip()
                    mitigation = item.get("mitigation")
                    if not risk:
                        raise ValueError("Risk descriptions cannot be empty.")
                    if mitigation is not None:
                        if not isinstance(mitigation, str):
                            raise ValueError("Risk mitigations must be text.")
                        mitigation = mitigation.strip()
                        if mitigation:
                            risk = f"{risk} (mitigation: {mitigation})"
                    normalized_risks.append(risk)
                else:
                    normalized_risks.append(item)
            value = normalized_risks
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("Text values cannot be empty.")
            return value.strip()
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            normalized = [item.strip() for item in value]
            if any(not item for item in normalized):
                raise ValueError("Text lists cannot contain empty values.")
            return normalized
        return value


class AgentPlanRequirement(AgentPlanModel):
    id: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    description: str = Field(..., min_length=1, max_length=1000)
    category: Literal[
        "functional",
        "technical",
        "quality",
        "documentation",
        "analysis",
    ]


class AgentAcceptanceCriterionResult(AgentPlanModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(..., min_length=1, max_length=1000)
    status: Literal["met", "not_met", "not_verified"]
    evidence: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_evidence_for_assessed_results(self):
        if (
            self.status in {"met", "not_met"}
            and (self.evidence is None or not self.evidence.strip())
        ):
            raise ValueError("Acceptance criterion assessments require evidence.")
        return self


class AgentPlanTask(AgentPlanModel):
    id: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    task_type: Literal[
        "implementation",
        "analysis",
        "verification",
        "setup",
        "documentation",
    ]
    description: str = Field(..., min_length=1, max_length=1000)
    objective: str = Field(..., min_length=1, max_length=2000)
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    requirement_ids: list[str] = Field(..., min_length=1, max_length=30)
    expected_files: list[str] = Field(default_factory=list, max_length=10)
    required_tools: list[
        Literal[
            "list_files",
            "read_file",
            "create_file",
            "update_file",
            "delete_file",
            "execute_command",
            "get_execution_result",
        ]
    ] = Field(default_factory=list, max_length=7)
    acceptance_criteria: list[str] = Field(..., min_length=1, max_length=10)
    acceptance_results: list[AgentAcceptanceCriterionResult] = Field(
        default_factory=list,
        max_length=10,
    )
    verification_steps: list[str] = Field(..., min_length=1, max_length=10)
    status: Literal[
        "pending",
        "in_progress",
        "applied",
        "completed",
        "blocked",
    ] = "pending"
    verification_status: Literal[
        "pending",
        "verified",
        "not_verified",
        "failed",
        "not_required",
    ] = "pending"


class AgentPlan(AgentPlanModel):
    plan_id: str = Field(
        default_factory=lambda: f"plan-{uuid4().hex[:16]}",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    objective: str = Field(..., min_length=1, max_length=2000)
    project_type: str = Field(..., min_length=1, max_length=500)
    scope: str = Field(..., min_length=1, max_length=2000)
    project_understanding: str = Field(..., min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    requirements: list[AgentPlanRequirement] = Field(..., min_length=1, max_length=30)
    acceptance_criteria: list[str] = Field(..., min_length=1, max_length=20)
    technical_approach: str = Field(..., min_length=1, max_length=4000)
    tasks: list[AgentPlanTask] = Field(..., min_length=1, max_length=12)
    risks: list[str] = Field(default_factory=list, max_length=10)
    validation_strategy: list[str] = Field(..., min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_task_graph(self):
        requirement_ids = [item.id for item in self.requirements]
        task_ids = [item.id for item in self.tasks]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Plan requirement IDs must be unique.")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Plan task IDs must be unique.")

        requirements_by_id = {item.id: item for item in self.requirements}
        tasks_by_id = {item.id: item for item in self.tasks}
        expected_file_owners: dict[str, str] = {}
        for task in self.tasks:
            if len(task.acceptance_criteria) != len(set(task.acceptance_criteria)):
                raise ValueError(
                    f"Task {task.id} has duplicate acceptance criteria."
                )
            if len(task.dependencies) != len(set(task.dependencies)):
                raise ValueError(f"Task {task.id} has duplicate dependencies.")
            if task.id in task.dependencies:
                raise ValueError(f"Task {task.id} cannot depend on itself.")
            missing_dependencies = set(task.dependencies) - tasks_by_id.keys()
            if missing_dependencies:
                raise ValueError(
                    f"Task {task.id} has unknown dependencies: "
                    f"{', '.join(sorted(missing_dependencies))}."
                )
            if len(task.requirement_ids) != len(set(task.requirement_ids)):
                raise ValueError(f"Task {task.id} has duplicate requirement IDs.")
            missing_requirements = set(task.requirement_ids) - requirements_by_id.keys()
            if missing_requirements:
                raise ValueError(
                    f"Task {task.id} references unknown requirements: "
                    f"{', '.join(sorted(missing_requirements))}."
                )
            if task.task_type == "implementation" and not task.expected_files:
                raise ValueError(
                    f"Implementation task {task.id} must declare expected files."
                )
            if len(task.expected_files) != len(set(task.expected_files)):
                raise ValueError(f"Task {task.id} has duplicate expected files.")
            for file_path in task.expected_files:
                prior_owner = expected_file_owners.get(file_path)
                if prior_owner is not None:
                    raise ValueError(
                        f"Expected file {file_path} is assigned to multiple tasks "
                        f"({prior_owner}, {task.id})."
                    )
                expected_file_owners[file_path] = task.id

        mapped_requirements = {
            requirement_id
            for task in self.tasks
            for requirement_id in task.requirement_ids
        }
        if mapped_requirements != set(requirement_ids):
            raise ValueError("Every plan requirement must map to at least one task.")
        for requirement in self.requirements:
            required_types = {
                "functional": {"implementation"},
                "technical": {"implementation"},
                "quality": {"implementation", "verification"},
                "documentation": {"documentation", "implementation"},
                "analysis": {"analysis"},
            }[requirement.category]
            if not any(
                requirement.id in task.requirement_ids
                and task.task_type in required_types
                for task in self.tasks
            ):
                raise ValueError(
                    f"Requirement {requirement.id} is not mapped to an appropriate task."
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("Plan task dependencies contain a cycle.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in tasks_by_id[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self


class AgentPlanningResult(AgentPlanModel):
    status: Literal["ready", "clarification_required"]
    plan: AgentPlan | None = Field(...)
    clarification_questions: list[str] = Field(..., max_length=3)
    actions: list[CodeAction] = Field(..., max_length=40)
    validation_command: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_result(self):
        if self.status == "ready":
            if self.plan is None:
                raise ValueError("A ready planning result requires a structured plan.")
            if self.clarification_questions:
                raise ValueError("A ready planning result cannot ask for clarification.")
        else:
            if not self.clarification_questions:
                raise ValueError(
                    "A clarification result requires at least one focused question."
                )
            if self.plan is not None or self.actions or self.validation_command:
                raise ValueError(
                    "A clarification result cannot include a plan, actions, or command."
                )
        return self


class AgentTaskCreate(BaseModel):
    project_id: int = Field(..., gt=0)
    task: str = Field(..., min_length=1, max_length=5000)
    context: str | None = Field(default=None, max_length=30000)
    current_file_path: str | None = Field(default=None, max_length=2000)
    selected_code: SelectedCodeContext | None = None
    terminal_context: TerminalContext | None = None


class AgentApproval(BaseModel):
    action_indexes: list[int] = Field(default_factory=list, max_length=10)
    reject_indexes: list[int] = Field(default_factory=list, max_length=10)
    accept_all: bool = False
    reject_all: bool = False


class AgentCommandApproval(BaseModel):
    command: str = Field(..., min_length=1, max_length=500)
    approved: bool


class AgentClarification(BaseModel):
    answer: str = Field(..., min_length=1, max_length=5000)


class AgentAction(BaseModel):
    action: CodeAction
    status: Literal[
        "scheduled",
        "awaiting_approval",
        "applied",
        "reverted",
        "rejected",
        "failed",
        "blocked",
    ]
    plan_task_id: str | None = None
    error: str | None = None


class AgentChangeResult(BaseModel):
    path: str
    content: str
    file_id: int
    deleted: bool = False


class AgentTaskResponse(BaseModel):
    id: str
    project_id: int
    task: str
    mode: Literal["agent"]
    status: Literal[
        "pending",
        "planning",
        "awaiting_approval",
        "awaiting_clarification",
        "awaiting_validation",
        "executing",
        "validating",
        "paused",
        "failed",
        "completed",
        "cancelled",
    ]
    plan: list[str]
    plan_details: AgentPlan | None = None
    task_history: list[dict[str, object]] = Field(default_factory=list)
    verification_status: Literal[
        "pending",
        "verified",
        "not_verified",
        "failed",
    ] = "pending"
    clarification_questions: list[str] = Field(default_factory=list)
    actions: list[AgentAction]
    validation_command: str | None = None
    validation_result: dict[str, object] | None = None
    preview_url: str | None = None
    change_history: list[dict[str, object]]
    iteration: int
    steps: list[dict[str, object]]
    stop_reason: str | None = None
    created_at: str
    updated_at: str
    changes: list[AgentChangeResult] = Field(default_factory=list)
    tool_calls_used: int = 0