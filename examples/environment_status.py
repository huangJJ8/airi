"""Generate an acceptance report; never replace missing Spark evidence with Mock output."""

from pathlib import Path

from airi.core.config import Settings
from airi.environments.service import SparkEnvironmentProbe
from airi.infrastructure.query_executor import SparkSQLExecutor

if __name__ == "__main__":
    settings = Settings()
    if settings.effective_execution_mode != "spark_test":
        # Offline report: intentionally no configured endpoint or network request.
        settings = Settings(
            _env_file=None, execution_mode="disabled", spark_host="", spark_test_host=""
        )
    report = SparkEnvironmentProbe(SparkSQLExecutor(settings), settings).validate()
    target = Path("examples/environment_validation_report.json")
    target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(report.overall)
