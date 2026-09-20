"""Isolated relationship probes for the joined-metric test types.

Every check here executes the *approved* SQL against a purpose-built synthetic
relationship fixture through the same mock executor the rest of the test suite
uses, then compares it to an independently computed expectation. Nothing looks at
SQL text, and nothing is satisfied by a substring match.

The `join` probe additionally executes a deliberately wrong statement — the naive
`enterprise_id = related_enterprise_id` join that skips the natural person — and
requires the approved result to differ from it. That is the only way to show the
result really travels through ``person_id`` rather than merely looking plausible.
"""

from collections import defaultdict

from airi.infrastructure.query_executor import MockQueryExecutor

BASE_TABLE = "enterprise_person_relation"
JOINED_TABLE = "person_enterprise_relation"

# A statement that joins the two sources directly on the enterprise identifier.
# It exists ONLY to be proven different from the approved draft; it is never
# returned, stored or rendered from the Metric IR.
NAIVE_DIRECT_JOIN = (
    f"SELECT ep.`enterprise_id` AS entity_id, "
    f"COUNT(DISTINCT pe.`related_enterprise_id`) AS `related_enterprise_count` "
    f"FROM `demo`.`{BASE_TABLE}` AS ep "
    f"INNER JOIN `demo`.`{JOINED_TABLE}` AS pe "
    f"ON ep.`enterprise_id` = pe.`related_enterprise_id` "
    f"GROUP BY ep.`enterprise_id`"
)


def _tables(base, joined):
    return {
        BASE_TABLE: [
            {"enterprise_id": enterprise, "person_id": person, "relation_type": "controller"}
            for enterprise, person in base
        ],
        JOINED_TABLE: [
            {"person_id": person, "related_enterprise_id": target, "relation_type": "shareholder"}
            for person, target in joined
        ],
    }


def _rows(code, tables):
    """Execute the approved draft against one synthetic fixture."""
    output = MockQueryExecutor.evaluate(code, [], 1000, tables)
    return {row["entity_id"]: row["related_enterprise_count"] for row in output.rows}


def person_mediated_count(tables) -> dict[str, int]:
    """Independent oracle: enterprise -> its persons -> their other enterprises."""
    persons = defaultdict(set)
    for row in tables[BASE_TABLE]:
        persons[row["enterprise_id"]].add(row["person_id"])
    targets = defaultdict(set)
    for row in tables[JOINED_TABLE]:
        targets[row["person_id"]].add(row["related_enterprise_id"])
    counts = {}
    for enterprise, linked in persons.items():
        related = set().union(*(targets[person] for person in linked)) - {enterprise}
        if related:
            counts[enterprise] = len(related)
    return counts


def probe_join(code):
    """The draft must travel through the natural person, not the enterprise id."""
    tables = _tables(
        base=[("E1", "P1"), ("E2", "P2")],
        joined=[("P1", "E2"), ("P2", "E2")],
    )
    approved = _rows(code, tables)
    expected = person_mediated_count(tables)
    naive = _rows(NAIVE_DIRECT_JOIN, tables)
    if approved != expected:
        return (
            "join_mismatch",
            f"Approved result {approved} differs from the person-mediated {expected}",
        )
    if approved == naive:
        return (
            "join_mismatch",
            "Approved draft is indistinguishable from a direct enterprise-id join",
        )
    return None, "Person-mediated path confirmed against a direct-join control"


def probe_distinct(code):
    """Repeated relation rows and several persons must not inflate the count."""
    tables = _tables(
        base=[("E1", "P1"), ("E1", "P2")],
        joined=[("P1", "E2"), ("P1", "E2"), ("P1", "E2"), ("P2", "E2")],
    )
    approved = _rows(code, tables)
    if approved != {"E1": 1}:
        return "distinct_mismatch", f"Duplicate relation rows changed the count: {approved}"
    return None, "Duplicate relation rows collapsed to one distinct enterprise"


def probe_self_exclusion(code):
    """An enterprise that only relates back to itself must not count itself."""
    tables = _tables(
        base=[("E1", "P1")],
        joined=[("P1", "E1")],
    )
    approved = _rows(code, tables)
    if approved:
        return "self_exclusion_mismatch", f"Self-referencing relation counted: {approved}"
    return None, "Self-referencing relation excluded"


def probe_missing_relation(code):
    """Enterprises absent from the relationship path must not appear as zero."""
    tables = _tables(
        base=[("E1", "P1"), ("E2", "P2")],
        joined=[("P1", "E3")],
    )
    approved = _rows(code, tables)
    if approved != {"E1": 1}:
        return (
            "missing_relation_mismatch",
            f"Enterprises without a usable relationship path were reported: {approved}",
        )
    return None, "Enterprises without a usable relationship path stay absent"


PROBES = {
    "join": probe_join,
    "distinct": probe_distinct,
    "self_exclusion": probe_self_exclusion,
    "missing_relation": probe_missing_relation,
}

RELATION_TEST_TYPES = tuple(PROBES)
