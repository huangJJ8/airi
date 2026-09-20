import math
from datetime import date, datetime
from decimal import Decimal


class ResultNormalizer:
    """Decimal stays exact as text; temporal values use ISO 8601, never implicit float."""

    @staticmethod
    def cell(value):
        if value is None or isinstance(value, (str, int)):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("Non-finite decimal")
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, float) and math.isfinite(value):
            return value
        raise ValueError("Unsupported result value")

    @staticmethod
    def type_name(description):
        kind = str(description[1]).lower()
        if kind == "decimal" and len(description) > 5 and description[4] is not None:
            return f"decimal({description[4]},{description[5]})"
        return kind
