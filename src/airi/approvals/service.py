from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from airi.approvals.artifacts import (
    DevelopmentArtifact,
    ReviewedArtifact,
    artifact_content_hash,
    canonical_hash,
)
from airi.approvals.persistence import ApprovalRecordRow, DevelopmentArtifactRow
from airi.approvals.schemas import ApprovalRecord, ReviewDecision, SubmitReview
from airi.core.exceptions import (
    ApprovalArtifactChangedError,
    ApprovalNotFoundError,
    ApprovalStateError,
)


def utcnow() -> datetime:
    # Store UTC without offset for MySQL/SQLite portability; API adds UTC back.
    return datetime.now(UTC).replace(tzinfo=None)


class ApprovalService:
    def __init__(self, session: Session):
        self.session = session

    def save_draft(self, result) -> None:
        self.session.add(
            DevelopmentArtifactRow(
                artifact_id=result.artifact.artifact_id,
                workflow_run_id=result.workflow_run_id,
                artifact_version=result.artifact.artifact_version,
                metric_name=result.metric_ir.name,
                metric_ir_hash=canonical_hash(result.metric_ir),
                artifact_hash=result.artifact.content_hash,
                status="draft",
                snapshot=result.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        self.session.commit()

    def _artifact(self, artifact_id: str) -> DevelopmentArtifactRow:
        row = self.session.get(DevelopmentArtifactRow, artifact_id, populate_existing=True)
        if row is None:
            raise ApprovalNotFoundError("Artifact not found")
        return row

    def _verify(self, row: DevelopmentArtifactRow, ir_hash: str, sql_hash: str) -> None:
        try:
            artifact = DevelopmentArtifact.model_validate(row.snapshot["artifact"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ApprovalArtifactChangedError("Stored artifact schema was changed") from exc
        valid = (
            canonical_hash(row.snapshot["metric_ir"]) == row.metric_ir_hash == ir_hash
            and artifact_content_hash(artifact)
            == row.artifact_hash
            == sql_hash
            == artifact.content_hash
            and artifact.artifact_id == row.artifact_id
            and artifact.artifact_version == row.artifact_version
            and row.snapshot["workflow_run_id"] == row.workflow_run_id
            and artifact.metric_name == row.metric_name == row.snapshot["metric_ir"]["name"]
        )
        if not valid:
            raise ApprovalArtifactChangedError(
                "Artifact or Metric IR content no longer matches review"
            )

    def _view(self, row: ApprovalRecordRow) -> ApprovalRecord:
        data = {name: getattr(row, name) for name in ApprovalRecord.model_fields}
        for name in ("created_at", "reviewed_at"):
            if data[name] is not None:
                data[name] = data[name].replace(tzinfo=UTC)
        return ApprovalRecord.model_validate(data)

    def get(self, approval_id: str) -> ApprovalRecord:
        row = self.session.get(ApprovalRecordRow, approval_id, populate_existing=True)
        if row is None:
            raise ApprovalNotFoundError("Approval not found")
        return self._view(row)

    def get_artifact(self, artifact_id: str) -> ReviewedArtifact:
        row = self._artifact(artifact_id)
        self._verify(row, row.metric_ir_hash, row.artifact_hash)
        return ReviewedArtifact.model_validate(row.snapshot["artifact"] | {"status": row.status})

    def submit(self, request: SubmitReview) -> ApprovalRecord:
        artifact = self._artifact(request.artifact_id)
        self._verify(artifact, request.metric_ir_hash, request.artifact_hash)
        if not artifact.snapshot["validation"]["valid"]:
            raise ApprovalStateError("Only validated drafts can be submitted")
        changed = self.session.execute(
            update(DevelopmentArtifactRow)
            .where(
                DevelopmentArtifactRow.artifact_id == artifact.artifact_id,
                DevelopmentArtifactRow.status == "draft",
            )
            .values(status="pending_review")
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise ApprovalStateError("Only draft can enter pending_review")
        row = ApprovalRecordRow(
            approval_id=str(uuid4()),
            workflow_run_id=artifact.workflow_run_id,
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.artifact_version,
            metric_name=artifact.metric_name,
            metric_ir_hash=artifact.metric_ir_hash,
            artifact_hash=artifact.artifact_hash,
            reviewer=None,
            comment=None,
            decision="pending",
            created_at=utcnow(),
            reviewed_at=None,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ApprovalStateError("Artifact already submitted") from exc
        return self._view(row)

    def decide(self, approval_id: str, decision: str, request: ReviewDecision) -> ApprovalRecord:
        if decision not in {"approved", "rejected"}:
            raise ApprovalStateError("Decision must be approved or rejected")
        row = self.session.get(ApprovalRecordRow, approval_id, populate_existing=True)
        if row is None:
            raise ApprovalNotFoundError("Approval not found")
        artifact = self._artifact(row.artifact_id)
        self._verify(artifact, row.metric_ir_hash, row.artifact_hash)
        if row.decision != "pending":
            raise ApprovalStateError("Only pending reviews accept a decision")
        changed = self.session.execute(
            update(DevelopmentArtifactRow)
            .where(
                DevelopmentArtifactRow.artifact_id == row.artifact_id,
                DevelopmentArtifactRow.status == "pending_review",
            )
            .values(status=decision)
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise ApprovalStateError("Artifact is not pending_review")
        row.decision, row.reviewer, row.comment = decision, request.reviewer, request.comment
        row.reviewed_at = utcnow()
        try:
            self.session.commit()
        except StaleDataError as exc:
            self.session.rollback()
            raise ApprovalStateError("Review was already decided concurrently") from exc
        return self._view(row)

    def require_approved(self, approval_id: str, artifact_id: str) -> ApprovalRecord:
        record = self.get(approval_id)
        if record.artifact_id != artifact_id:
            raise ApprovalArtifactChangedError("Approval is bound to a different artifact")
        artifact = self._artifact(artifact_id)
        self._verify(artifact, record.metric_ir_hash, record.artifact_hash)
        if record.decision != "approved" or artifact.status != "approved":
            raise ApprovalStateError("Artifact has not been approved")
        return record
