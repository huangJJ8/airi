import re
from collections import Counter
from decimal import Decimal

from airi.environments.fixtures import fixture_rows
from airi.environments.models import PartitionAdvisory, SourceSchema


class MetadataFailure(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


class SchemaInspector:
    def inspect(self, output, *, fixture=False):
        if output.truncated:
            raise MetadataFailure("metadata_error")
        columns, partitions, in_partition = {}, set(), False
        for row in output.rows:
            values = list(row.values())
            if len(values) < 2:
                continue
            name, kind = str(values[0] or "").strip(), str(values[1] or "").lower().strip()
            if name.startswith("# Partition"):
                in_partition = True
            if not name or name.startswith("#"):
                continue
            if in_partition:
                partitions.add(name)
            columns[name] = kind
        if not columns:
            raise MetadataFailure("metadata_error")
        decimal = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", columns.get("invoice_amt", ""))
        return SourceSchema(
            decimal_precision=int(decimal[1]) if decimal else None,
            decimal_scale=int(decimal[2]) if decimal else None,
            database="tmp_db" if fixture else "c_db",
            table="airi_invoice_fixture" if fixture else "source_fp_jdc_view",
            columns=columns,
            partition=PartitionAdvisory(
                available="dt" in columns, type=columns.get("dt"), is_partition="dt" in partitions
            ),
        )


class MetricMetadataValidator:
    def validate(self, schema, timezone):
        required = {"seller_tax_no", "invoice_amt", "invoice_date", "dt"}
        if not required.issubset(schema.columns):
            raise MetadataFailure("metadata_error")
        numeric = r"decimal\(\d+,\s*\d+\)|decimal|double|float|bigint|int|smallint|tinyint"
        if not re.fullmatch(numeric, schema.columns["invoice_amt"]):
            raise MetadataFailure("unsupported_source_schema")
        if not re.fullmatch(
            r"string|varchar(?:\(\d+\))?|char(?:\(\d+\))?", schema.columns["seller_tax_no"]
        ):
            raise MetadataFailure("unsupported_source_schema")
        if schema.columns["invoice_date"] not in {"date", "timestamp"}:
            raise MetadataFailure("unsupported_source_schema")
        # Conservative gate for both DATE and TIMESTAMP; never issue SET or casts.
        if timezone != "Asia/Shanghai":
            raise MetadataFailure("timezone_mismatch")


def fixture_matches(output):
    if output.truncated:
        return False

    def normalized(row):
        return (
            row["seller_tax_no"],
            str(row["invoice_date"]),
            None if row["invoice_amt"] is None else Decimal(str(row["invoice_amt"])),
            str(row["dt"]),
        )

    try:
        return Counter(map(normalized, output.rows)) == Counter(map(normalized, fixture_rows()))
    except (KeyError, ValueError, ArithmeticError):
        return False
