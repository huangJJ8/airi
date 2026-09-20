import json
from importlib.resources import files

from airi.core.schemas import StrictSchema


class FixtureSourceMapping(StrictSchema):
    enabled: bool = False

    def apply(self, code: str) -> str:
        # Called only after the original artifact passes the closed Sandbox grammar.
        if not self.enabled:
            return code
        import re

        return re.sub(
            r"`?c_db`?\s*\.\s*`?source_fp_jdc_view`?",
            "`tmp_db`.`airi_invoice_fixture`",
            code,
            flags=re.I,
        )


def fixture_rows():
    return json.loads(files("airi.environments").joinpath("invoices.json").read_text("utf-8"))
