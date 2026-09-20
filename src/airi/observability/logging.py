import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for name in (
            "request_id",
            "temporal_validation_run_id",
            "series_id",
            "time_slice_id",
            "promotion_review_id",
            "reflection_run_id",
            "refinement_run_id",
            "proposal_id",
            "decision_id",
            "baseline_artifact_id",
            "baseline_experiment_run_id",
            "candidate_artifact_id",
            "candidate_test_run_id",
            "candidate_experiment_run_id",
            "outcome",
            "experiment_run_ids",
            "artifact_ids",
            "dataset_snapshot_ids",
            "experiment_spec_id",
            "experiment_run_id",
            "dataset_snapshot_id",
            "validation_run_id",
            "provider_query_id",
            "method",
            "status_code",
            "duration_ms",
            "error_type",
            "workflow_run_id",
            "artifact_id",
            "approval_id",
            "execution_run_id",
            "test_run_id",
            "status",
            "row_count",
            "truncated",
            "failure_category",
            "passed",
            "failed",
            "skipped",
            # Phase 7 production lifecycle identifiers. IDs and severities only:
            # never SQL, entity rows, distributions, notes or alert evidence.
            "actor_id",
            "production_deployment_id",
            "deployment_review_id",
            "rollback_review_id",
            "monitoring_snapshot_id",
            "alert_id",
            "alert_severity",
            "feedback_event_id",
            "environment_id",
            "metric_definition_id",
            "metric_version_id",
            "release_id",
            "target_version",
        ):
            if hasattr(record, name):
                entry[name] = getattr(record, name)
        # No request bodies, authorization headers, DB URLs or raw exception messages.
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    logger = logging.getLogger("airi")
    if not any(getattr(handler, "_airi_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._airi_handler = True
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
