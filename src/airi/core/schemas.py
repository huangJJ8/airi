from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Version = Annotated[
    str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
]
NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class StrictSchema(BaseModel):
    """Reject unknown fields and implicit scalar coercion at domain boundaries."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, validate_default=True, allow_inf_nan=False
    )


class VersionedReference(StrictSchema):
    name: Identifier
    version: Version
