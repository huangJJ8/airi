import logging
from datetime import UTC, datetime

from pydantic import ValidationError

from airi.approvals.artifacts import canonical_hash
from airi.approvals.service import utcnow
from airi.core.exceptions import LLMConfigurationError, LLMServiceError
from airi.reflection.diagnostics import ReflectionDiagnostics, evidence_references
from airi.reflection.evidence import (
    ComparisonCompatibilityGuard,
    EvidenceExtractor,
    ReflectionError,
    decode,
    get_row,
)
from airi.reflection.models import (
    ReflectionContext,
    ReflectionLLMOutput,
    ReflectionReport,
    ReflectionRun,
)
from airi.reflection.persistence import ReflectionReportRow, ReflectionRunRow
from airi.reflection.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from airi.reflection.validation import RefinementProposalValidator


class ReflectionWorkflow:
    def __init__(self, session, skills, llm, policy, model, output_kind):
        self.session, self.skills, self.llm = session, skills, llm
        self.policy, self.model, self.output_kind = policy, model, output_kind

    def get(self, run_id):
        return decode(
            ReflectionReport, get_row(self.session, ReflectionReportRow, run_id).report_json
        )

    def run(self, spec, request_id):
        extractor = EvidenceExtractor(self.session, self.skills)
        evidence = [extractor.extract(run_id) for run_id in sorted(spec.experiment_run_ids)]
        comparison = (
            ComparisonCompatibilityGuard().validate(evidence) if spec.mode == "comparison" else None
        )
        diagnostics = ReflectionDiagnostics().analyze(evidence, self.policy)
        if comparison:
            diagnostics.extend(ReflectionDiagnostics().compare(evidence))
        missing = [
            "No independent out-of-time or cross-period stability evidence is supplied.",
            "Business reversal and duplicate-invoice rules require human confirmation.",
        ]
        if any(e.synthetic_data for e in evidence):
            missing.extend(
                [
                    "Synthetic experiments cannot establish real financial effectiveness.",
                    "Real Spark results and financial labels are not verified by this evidence.",
                ]
            )
        context = ReflectionContext(
            evidence=evidence,
            diagnostics=diagnostics,
            comparison=comparison,
            policy=self.policy,
            references=evidence_references(evidence),
            missing_evidence=missing,
        )
        run = ReflectionRun(
            mode=spec.mode,
            status="running",
            reflection_policy_version=self.policy.version,
            prompt_version=PROMPT_VERSION,
            model=self.model,
            input_evidence_hash=canonical_hash(context),
        )
        row = ReflectionRunRow(
            **run.model_dump(exclude={"created_at", "finished_at"}),
            created_at=utcnow(),
            finished_at=None,
        )
        self.session.add(row)
        self.session.commit()
        output, status, warnings = None, "unavailable", []
        try:
            raw = self.llm.complete_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=context.model_dump_json(),
                schema=ReflectionLLMOutput.model_json_schema(),
            )
            output = RefinementProposalValidator().validate(
                ReflectionLLMOutput.model_validate_json(raw), context
            )
            status = "completed"
        except (ValidationError, ReflectionError):
            status = "rejected"
            warnings.append("reflection_output_rejected; no model content retained")
        except (LLMConfigurationError, LLMServiceError, TimeoutError, ConnectionError):
            warnings.append("reflection_provider_unavailable; deterministic diagnostics only")
        run = run.model_copy(
            update={
                "status": "completed" if output else "diagnostics_only",
                "finished_at": datetime.now(UTC),
            }
        )
        provenance = {
            "experiment_run_ids": [e.experiment_run_id for e in evidence],
            "experiment_report_hashes": {
                e.experiment_run_id: e.experiment_report_hash for e in evidence
            },
            "artifact_ids": [e.artifact_id for e in evidence],
            "artifact_versions": [e.artifact_version for e in evidence],
            "metric_ir_hashes": [e.metric_ir_hash for e in evidence],
            "dataset_snapshot_ids": [e.dataset_snapshot_id for e in evidence],
            "label_definition_ids": [e.label_definition_id for e in evidence],
            "evaluation_policy_versions": [e.evaluation_policy.version for e in evidence],
            "reflection_policy": self.policy.model_dump(mode="json"),
            "reflection_policy_version": self.policy.version,
            "reflection_prompt_version": PROMPT_VERSION,
            "model": self.model,
            "input_evidence_hash": run.input_evidence_hash,
            "scenario_knowledge_hashes": [canonical_hash(e.scenario_skill) for e in evidence],
        }
        report = ReflectionReport(
            run=run,
            experiment_run_ids=provenance["experiment_run_ids"],
            evidence=evidence,
            diagnostics=diagnostics,
            comparison=comparison,
            references=context.references,
            summary="Research diagnostics from persisted experiments; hypotheses are unverified. "
            "Human review is required; no refinement or threshold is applied.",
            llm_summary=output.summary if output else None,
            llm_reflection_status=status,
            llm_output_kind=self.output_kind,
            hypotheses=output.hypotheses if output else [],
            refinement_proposals=output.proposals if output else [],
            missing_evidence=list(
                dict.fromkeys(missing + (output.missing_evidence if output else []))
            ),
            provenance=provenance,
            warnings=warnings,
        )
        row.status, row.finished_at = run.status, utcnow()
        self.session.add(
            ReflectionReportRow(
                reflection_run_id=run.reflection_run_id,
                report_json=report.model_dump(mode="json"),
                summary=report.summary,
                proposal_count=len(report.refinement_proposals),
                requires_human_review=True,
                created_at=utcnow(),
            )
        )
        self.session.commit()
        logging.getLogger("airi.reflection").info(
            "reflection_completed",
            extra={
                "request_id": request_id,
                "reflection_run_id": run.reflection_run_id,
                "experiment_run_ids": provenance["experiment_run_ids"],
                "artifact_ids": provenance["artifact_ids"],
                "dataset_snapshot_ids": provenance["dataset_snapshot_ids"],
                "status": run.status,
            },
        )
        return report
