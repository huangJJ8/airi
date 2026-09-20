class AIRIError(Exception):
    code = "airi_error"
    status_code = 400
    public_message: str | None = None

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DuplicateRegistrationError(AIRIError):
    code = "duplicate_registration"
    status_code = 409


class RegistryNotFoundError(AIRIError):
    code = "registry_not_found"
    status_code = 404


class ToolInputError(AIRIError):
    code = "tool_input_invalid"
    status_code = 422


class ToolOutputError(AIRIError):
    code = "tool_output_invalid"
    status_code = 500


class RequirementParseError(AIRIError):
    code = "requirement_parse_failed"
    status_code = 422


class UnsupportedMetricError(AIRIError):
    code = "unsupported_metric"
    status_code = 422


class LLMConfigurationError(AIRIError):
    code = "llm_not_configured"
    status_code = 503
    public_message = "Configure AIRI_LLM_MODEL before generating a draft"


class LLMServiceError(AIRIError):
    code = "llm_service_failed"
    status_code = 502
    public_message = "LLM request failed, was refused, or returned an incomplete response"


class PlanningError(AIRIError):
    code = "planning_failed"
    status_code = 422


class SkillDependencyError(AIRIError):
    code = "skill_dependency_invalid"
    status_code = 422


class SQLValidationError(AIRIError):
    code = "sql_validation_failed"
    status_code = 422


class ApprovalNotFoundError(AIRIError):
    code = "approval_not_found"
    status_code = 404


class ApprovalStateError(AIRIError):
    code = "approval_invalid_state"
    status_code = 409


class ApprovalArtifactChangedError(AIRIError):
    code = "approval_artifact_changed"
    status_code = 409


class DerivedMetricError(AIRIError):
    code = "unsupported_derived_metric"
    status_code = 422


class DerivedDependencyError(AIRIError):
    code = "derived_metric_dependency_invalid"
    status_code = 422


class ZeroDivisionStrategyError(AIRIError):
    code = "zero_division_strategy_invalid"
    status_code = 422
