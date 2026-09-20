import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from airi.core.schemas import Identifier, Version
from airi.tools.spark_sql import SQLDraft


def canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DevelopmentArtifact(SQLDraft):
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    artifact_version: Version = "1.0.0"
    metric_name: Identifier
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewedArtifact(DevelopmentArtifact):
    status: Literal["draft", "pending_review", "approved", "rejected"]


def artifact_content_hash(artifact: SQLDraft) -> str:
    # Identity, lifecycle status and timestamps are deliberately excluded.
    content = artifact.model_dump(
        mode="json",
        include={
            "type",
            "language",
            "code",
            "template",
            "warnings",
            "metric_name",
            "artifact_version",
        },
    )
    return canonical_hash(content)


def identify_artifact(draft: SQLDraft, metric_name: str) -> DevelopmentArtifact:
    data = draft.model_dump() | {"metric_name": metric_name, "artifact_version": "1.0.0"}
    provisional = DevelopmentArtifact(**data, content_hash="0" * 64)
    return provisional.model_copy(update={"content_hash": artifact_content_hash(provisional)})
