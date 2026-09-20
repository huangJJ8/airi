from datetime import datetime, time, timedelta, timezone
from typing import Literal, Self

from pydantic import AwareDatetime, field_validator, model_validator

from airi.core.schemas import StrictSchema

# Only contemporary Shanghai calendar-day windows are supported in this slice.
# Shanghai has no DST in the explicitly accepted range (2000 onwards).
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class ExecutionContext(StrictSchema):
    anchor_time: AwareDatetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @field_validator("anchor_time", mode="before")
    @classmethod
    def parse_iso_datetime(cls, value: object) -> object:
        # Explicit wire-format conversion; all other domain types stay strict.
        if isinstance(value, str):
            if "T" not in value:
                raise ValueError("anchor_time must be an ISO-8601 datetime with offset")
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("anchor_time must be an ISO-8601 datetime with offset") from None
        return value

    @model_validator(mode="after")
    def require_local_midnight(self) -> Self:
        local = self.anchor_time.astimezone(SHANGHAI)
        if not 2000 <= local.year <= 9998 or local.time() != time(0):
            raise ValueError("anchor_time must be Shanghai midnight, in years 2000..9998")
        return self
