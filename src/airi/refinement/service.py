import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from airi.approvals.artifacts import canonical_hash
from airi.approvals.persistence import ApprovalRecordRow, DevelopmentArtifactRow
from airi.approvals.service import ApprovalService, utcnow
from airi.core.execution import ExecutionContext
from airi.execution.models import ExecutionRequest
from airi.experiments.models import ExperimentReport, ExperimentSpec
from airi.experiments.persistence import ExperimentEvaluationRow, ExperimentSpecRow
from airi.experiments.service import ExperimentService
from airi.refinement.comparison import BaselineCandidateComparison
from airi.refinement.models import (
    CandidateArtifact,
    CandidateExperiment,
    CandidateMetricDefinition,
    ProposalDecision,
    ProposalView,
    RefinementComparisonPolicy,
    RefinementPlan,
    RefinementReport,
    RefinementRun,
)
from airi.refinement.persistence import ProposalDecisionRow, RefinementReportRow, RefinementRunRow
from airi.refinement.transform import (
    CandidateIRTransformer,
    ProposalCapabilityMatrix,
    RefinementError,
)
from airi.reflection.evidence import EvidenceExtractor, decode, get_row
from airi.reflection.models import ReflectionReport
from airi.reflection.persistence import ReflectionReportRow
from airi.workflows.testing.workflow import TestingWorkflow


class RefinementService:
    def __init__(self, session, state):
        self.session, self.state = session, state

    def proposals(self, reflection_id):
        stored = get_row(self.session, ReflectionReportRow, reflection_id)
        report = decode(ReflectionReport, stored.report_json)
        if report.run.status != "completed" or report.llm_reflection_status != "completed":
            raise RefinementError("proposal_not_found")
        result = []
        for index, proposal in enumerate(report.refinement_proposals):
            pid = canonical_hash(
                {
                    "reflection_run_id": reflection_id,
                    "index": index,
                    "proposal": proposal.model_dump(mode="json"),
                }
            )
            matches = [e for e in report.evidence if e.evaluation.metric_name == proposal.target]
            windows = (
                ProposalCapabilityMatrix().windows(proposal, matches[0].metric_ir)
                if len(matches) == 1
                else []
            )
            decision = self.session.scalar(
                select(ProposalDecisionRow).where(
                    ProposalDecisionRow.reflection_run_id == reflection_id,
                    ProposalDecisionRow.proposal_id == pid,
                )
            )
            result.append(
                ProposalView(
                    proposal_id=pid,
                    proposal=proposal,
                    automation_capability="supported" if windows else "manual_only",
                    allowed_windows=windows,
                    decision=decode(ProposalDecision, decision.decision_json) if decision else None,
                )
            )
        return result

    def proposal(self, reflection_id, proposal_id):
        found = next(
            (p for p in self.proposals(reflection_id) if p.proposal_id == proposal_id), None
        )
        if found is None:
            raise RefinementError("proposal_not_found")
        return found

    def decide(self, reflection_id, proposal_id, payload):
        proposal = self.proposal(reflection_id, proposal_id)
        if proposal.decision is not None and proposal.decision.decision in (
            "accepted_for_investigation",
            "rejected",
        ):
            raise RefinementError("proposal_already_decided")
        decision = ProposalDecision(
            **payload.model_dump(),
            reflection_run_id=reflection_id,
            proposal_id=proposal_id,
            proposal_hash=canonical_hash(proposal.proposal),
        )
        if proposal.decision is not None:
            previous = proposal.decision
            if previous.decision == payload.decision:
                raise RefinementError("proposal_already_decided")
            decision = decision.model_copy(
                update={
                    "decision_id": previous.decision_id,
                    "previous_decisions": [
                        *previous.previous_decisions,
                        previous.model_dump(mode="json", exclude={"previous_decisions"}),
                    ],
                }
            )
            changed = self.session.execute(
                update(ProposalDecisionRow)
                .where(
                    ProposalDecisionRow.decision_id == previous.decision_id,
                    ProposalDecisionRow.decision == previous.decision,
                )
                .values(decision=decision.decision, decision_json=decision.model_dump(mode="json"))
            )
            if changed.rowcount != 1:
                self.session.rollback()
                raise RefinementError("proposal_already_decided")
            self.session.commit()
            return decision
        self.session.add(
            ProposalDecisionRow(
                decision_id=decision.decision_id,
                reflection_run_id=reflection_id,
                proposal_id=proposal_id,
                decision=decision.decision,
                proposal_hash=decision.proposal_hash,
                decision_json=decision.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise RefinementError("proposal_already_decided") from None
        return decision

    def inputs(self, request):
        view = self.proposal(request.reflection_run_id, request.proposal_id)
        if view.decision is None or view.decision.decision != "accepted_for_investigation":
            raise RefinementError("refinement_not_authorized")
        if view.automation_capability != "supported":
            raise RefinementError("proposal_manual_only")
        if request.parameter_selection.window_days not in view.allowed_windows:
            raise RefinementError("proposal_parameter_invalid")
        stored = get_row(self.session, ReflectionReportRow, request.reflection_run_id)
        reflection = decode(ReflectionReport, stored.report_json)
        baseline = next(
            e for e in reflection.evidence if e.evaluation.metric_name == view.proposal.target
        )
        actual = EvidenceExtractor(self.session, self.state.skills).extract(
            baseline.experiment_run_id
        )
        if (
            actual.experiment_report_hash != baseline.experiment_report_hash
            or canonical_hash(view.proposal) != view.decision.proposal_hash
        ):
            raise RefinementError("refinement_input_changed")
        return view, baseline, canonical_hash(stored.report_json), reflection

    def input_hash(self, request, view, baseline, reflection_hash, policy):
        return canonical_hash(
            {
                "reflection_report_hash": reflection_hash,
                "proposal": view.proposal.model_dump(mode="json"),
                "decision": view.decision.model_dump(mode="json"),
                "baseline_ir_hash": baseline.metric_ir_hash,
                "parameter_selection": request.parameter_selection.model_dump(),
                "comparison_policy": policy.model_dump(mode="json"),
            }
        )

    def create(self, request):
        view, baseline, reflection_hash, reflection = self.inputs(request)
        ir, diff = CandidateIRTransformer().transform_window(
            baseline.metric_ir, request.parameter_selection.window_days
        )
        artifact = get_row(self.session, DevelopmentArtifactRow, baseline.artifact_id)
        context = ExecutionContext.model_validate(artifact.snapshot["execution_context"])
        result = self.state.development_workflow.run_structured(ir, context, candidate=True)
        ApprovalService(self.session).save_draft(result)
        policy = RefinementComparisonPolicy()
        plan = RefinementPlan(
            request=request,
            decision=view.decision,
            baseline_artifact_id=baseline.artifact_id,
            baseline_experiment_run_id=baseline.experiment_run_id,
            reflection_report_hash=reflection_hash,
            proposal_hash=canonical_hash(view.proposal),
            comparison_policy=policy,
            refinement_input_hash=self.input_hash(request, view, baseline, reflection_hash, policy),
        )
        run = RefinementRun(
            reflection_run_id=request.reflection_run_id,
            proposal_id=request.proposal_id,
            decision_id=view.decision.decision_id,
            baseline_artifact_id=baseline.artifact_id,
            baseline_experiment_run_id=baseline.experiment_run_id,
            candidate_artifact_id=result.artifact.artifact_id,
            status="pending_candidate_review",
        )
        definition = CandidateMetricDefinition(
            baseline_artifact_id=baseline.artifact_id,
            baseline_experiment_run_id=baseline.experiment_run_id,
            reflection_run_id=request.reflection_run_id,
            proposal_id=request.proposal_id,
            metric_ir=ir,
            metric_ir_hash=canonical_hash(ir),
            semantic_diff=diff,
        )
        report = RefinementReport(
            run=run,
            plan=plan,
            definition=definition,
            candidate=CandidateArtifact(
                artifact_id=result.artifact.artifact_id,
                artifact_version=result.artifact.artifact_version,
                content_hash=result.artifact.content_hash,
                metric_ir_hash=canonical_hash(ir),
                baseline_artifact_id=baseline.artifact_id,
                refinement_run_id=run.refinement_run_id,
                proposal_id=request.proposal_id,
            ),
            synthetic_data=baseline.synthetic_data,
            warnings=["Synthetic research cannot establish real financial effectiveness."]
            if baseline.synthetic_data
            else [],
            missing_evidence=reflection.missing_evidence,
        )
        baseline_report = decode(
            ExperimentReport,
            get_row(self.session, ExperimentEvaluationRow, baseline.experiment_run_id).report_json,
        )
        baseline_spec = decode(
            ExperimentSpec,
            get_row(
                self.session, ExperimentSpecRow, baseline_report.run.experiment_spec_id
            ).spec_json,
        )
        report = report.model_copy(
            update={
                "experiment_context": {
                    **baseline_spec.model_dump(
                        mode="json",
                        include={
                            "dataset_snapshot_id",
                            "label_definition_id",
                            "observation_time",
                            "label_window",
                            "evaluation",
                            "anchor_time",
                            "environment_validation_run_id",
                        },
                    ),
                    "execution_mode": baseline_report.execution_mode,
                }
            }
        )
        self.session.add(
            RefinementRunRow(
                **run.model_dump(exclude={"created_at", "finished_at", "failure_category"}),
                comparison_policy_version=policy.version,
                created_at=utcnow(),
                finished_at=None,
            )
        )
        self.session.add(
            RefinementReportRow(
                refinement_run_id=run.refinement_run_id,
                report_json=report.model_dump(mode="json"),
                outcome=None,
                coverage_delta=None,
                ks_delta=None,
                iv_delta=None,
                created_at=utcnow(),
            )
        )
        self.session.commit()
        return report

    def get(self, run_id):
        return decode(
            RefinementReport, get_row(self.session, RefinementReportRow, run_id).report_json
        )

    def save(self, report):
        row = get_row(self.session, RefinementRunRow, report.run.refinement_run_id)
        for field in ("status", "outcome", "candidate_test_run_id", "candidate_experiment_run_id"):
            setattr(row, field, getattr(report.run, field))
        row.finished_at = utcnow() if report.run.finished_at else None
        saved = get_row(self.session, RefinementReportRow, row.refinement_run_id)
        saved.report_json, saved.outcome = report.model_dump(mode="json"), report.run.outcome
        if report.comparison:
            for field in ("coverage_delta", "ks_delta", "iv_delta"):
                setattr(saved, field, getattr(report.comparison, field))
        self.session.commit()

    def evaluate(self, run_id, request_id):
        report = self.get(run_id)
        view, baseline, reflection_hash, _ = self.inputs(report.plan.request)
        if (
            self.input_hash(
                report.plan.request, view, baseline, reflection_hash, report.plan.comparison_policy
            )
            != report.plan.refinement_input_hash
        ):
            raise RefinementError("refinement_input_changed")
        original = decode(
            ExperimentReport,
            get_row(self.session, ExperimentEvaluationRow, baseline.experiment_run_id).report_json,
        )
        if self.state.settings.effective_execution_mode != original.execution_mode:
            raise RefinementError("comparison_invalid")
        approval = self.session.scalar(
            select(ApprovalRecordRow).where(
                ApprovalRecordRow.artifact_id == report.run.candidate_artifact_id,
                ApprovalRecordRow.decision == "approved",
            )
        )
        if approval is None:
            raise RefinementError("candidate_not_approved")
        ApprovalService(self.session).require_approved(
            approval.approval_id, report.run.candidate_artifact_id
        )
        candidate_row = get_row(
            self.session, DevelopmentArtifactRow, report.run.candidate_artifact_id
        )
        expected, _ = CandidateIRTransformer().transform_window(
            baseline.metric_ir, report.plan.request.parameter_selection.window_days
        )
        stored_run = get_row(self.session, RefinementRunRow, run_id)
        if (
            stored_run.candidate_artifact_id != report.run.candidate_artifact_id
            or report.run.baseline_artifact_id != baseline.artifact_id
            or report.run.baseline_experiment_run_id != baseline.experiment_run_id
            or candidate_row.artifact_hash != report.candidate.content_hash
            or candidate_row.metric_ir_hash != report.candidate.metric_ir_hash
            or candidate_row.snapshot["execution_context"]
            != original.reproducibility["execution_metadata"]["execution_context"]
        ):
            raise RefinementError("refinement_input_changed")
        if canonical_hash(candidate_row.snapshot["metric_ir"]) != canonical_hash(expected):
            raise RefinementError("candidate_semantic_diff_invalid")
        changed = self.session.execute(
            update(RefinementRunRow)
            .where(
                RefinementRunRow.refinement_run_id == run_id,
                RefinementRunRow.status == "pending_candidate_review",
            )
            .values(status="testing")
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise RefinementError("refinement_already_evaluated")
        self.session.commit()
        run = report.run.model_copy(update={"status": "testing"})
        try:
            tested = TestingWorkflow(
                self.session, self.state.query_executor, self.state.skills
            ).run(
                ExecutionRequest(
                    artifact_id=run.candidate_artifact_id,
                    approval_id=approval.approval_id,
                    max_rows=1000,
                ),
                request_id,
            )
            run = run.model_copy(update={"candidate_test_run_id": tested.report.test_run_id})
            if tested.report.status not in ("passed", "passed_with_warnings"):
                raise RefinementError("candidate_test_failed")
            spec = decode(
                ExperimentSpec,
                get_row(self.session, ExperimentSpecRow, original.run.experiment_spec_id).spec_json,
            )
            data = spec.model_dump(mode="json")
            data.pop("experiment_spec_id")
            data.update(
                experiment_name=expected.name + "_refinement",
                metric={
                    "artifact_id": run.candidate_artifact_id,
                    "artifact_version": report.candidate.artifact_version,
                },
                approval_id=approval.approval_id,
                test_run_id=tested.report.test_run_id,
            )
            experiment_service = ExperimentService(
                self.session, self.state.query_executor, self.state.settings
            )
            candidate_spec = experiment_service.create(decode(ExperimentSpec, data))
            run = run.model_copy(update={"status": "experimenting"})
            self.save(report.model_copy(update={"run": run}))
            experiment = experiment_service.run(candidate_spec.experiment_spec_id, request_id)
            run = run.model_copy(
                update={"candidate_experiment_run_id": experiment.run.experiment_run_id}
            )
            if experiment.run.status != "completed":
                raise RefinementError("refinement_evaluation_failed")
            if any(
                original.reproducibility.get(key) != experiment.reproducibility.get(key)
                for key in ("environment_validation_run_id", "environment_report_hash")
            ):
                raise RefinementError("comparison_invalid")
            if original.reproducibility["execution_metadata"].get(
                "source_mapping"
            ) != experiment.reproducibility["execution_metadata"].get("source_mapping"):
                raise RefinementError("comparison_invalid")
            candidate = EvidenceExtractor(self.session, self.state.skills).extract(
                experiment.run.experiment_run_id
            )
            comparison = BaselineCandidateComparison().compare(
                baseline, candidate, report.plan.comparison_policy
            )
            run = run.model_copy(
                update={
                    "status": "completed",
                    "outcome": comparison.outcome,
                    "finished_at": datetime.now(UTC),
                }
            )
            report = report.model_copy(
                update={
                    "run": run,
                    "comparison": comparison,
                    "warnings": list(dict.fromkeys([*report.warnings, *experiment.warnings])),
                    "candidate_experiment": CandidateExperiment(
                        experiment_spec_id=candidate_spec.experiment_spec_id,
                        experiment_run_id=experiment.run.experiment_run_id,
                        test_run_id=tested.report.test_run_id,
                        approval_id=approval.approval_id,
                    ),
                }
            )
        except Exception as exc:
            category = (
                exc.code if isinstance(exc, RefinementError) else "refinement_evaluation_failed"
            )
            report = report.model_copy(
                update={
                    "run": run.model_copy(
                        update={
                            "status": "failed",
                            "outcome": "inconclusive",
                            "failure_category": category,
                            "finished_at": datetime.now(UTC),
                        }
                    )
                }
            )
        self.save(report)
        logging.getLogger("airi.refinement").info(
            "refinement_evaluated", extra={"request_id": request_id, **report.run.model_dump()}
        )
        return report

    def final_decision(self, run_id, payload):
        report = self.get(run_id)
        if report.run.status not in ("completed", "failed") or report.final_decision is not None:
            raise RefinementError("refinement_decision_invalid")
        # Advisory decision only: no artifact mutation or execution.
        changed = self.session.execute(
            update(RefinementRunRow)
            .where(
                RefinementRunRow.refinement_run_id == run_id,
                RefinementRunRow.final_decision_recorded.is_(False),
                RefinementRunRow.status.in_(["completed", "failed"]),
            )
            .values(final_decision_recorded=True)
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise RefinementError("refinement_decision_invalid")
        report = report.model_copy(update={"final_decision": payload})
        self.save(report)
        return report
