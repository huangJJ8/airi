"""Synthetic two-hop relationship fixture for the ``enterprise_relation`` demo.

Everything here is fabricated for a local portfolio demo. Identifiers are
deliberately self-describing (``91310000SYNTHxxxx`` / ``SYNTHPERSONxxxx``) so no
real unified social credit code, person, customer or internal table name can
appear in a screenshot or an artifact. The synthetic schema is ``demo``.

Construction (deterministic; every count below follows from these rules alone):

    N = 120 enterprises E1..E120, plus ISO (an enterprise with no relations)
    person Pi is linked to Ei                       (enterprise_person_relation)
    Pi is also linked to E((i + k) mod N) for k = 1..t_i, where t_i = i % 5
    if i % 4 == 0: hub person HH_i is linked to Ei and to E(i+1)
    if i % 7 == 0: Pi is linked back to Ei          (a self-loop to exclude)
    if i % 3 == 0: Pi's first relation row is duplicated once

Contract: the metric returns one row per enterprise that has at least one
*non-self* related enterprise on the two-hop path. An enterprise with no such
relation (and ISO, which is not on the path at all) is **absent** from the
result — it is not reported as zero. Therefore

    E_i present  iff  t_i >= 1  or  i % 4 == 0
    related_count(E_i) = t_i, bumped to 1 when t_i == 0 and i % 4 == 0
    ISO and every i with t_i == 0 and i % 4 != 0 are absent
"""

from typing import NamedTuple

RELATION_PARTITION = "20260911"
ENTERPRISES = 120
DATABASE = "demo"
BASE_TABLE = "enterprise_person_relation"
JOINED_TABLE = "person_enterprise_relation"


class RelationFixture(NamedTuple):
    tables: dict[str, list[dict]]
    labeled_rows: list[dict]


def enterprise_id(index: int) -> str:
    return f"91310000SYNTH{index:04d}"


def person_id(index: int) -> str:
    return f"SYNTHPERSON{index:04d}"


def hub_person_id(index: int) -> str:
    return f"SYNTHPERSONH{index:04d}"


ISOLATED_ENTERPRISE = "91310000SYNTH9000"


def relation_tables() -> dict[str, list[dict]]:
    """The two synthetic relationship tables, exactly as declared in the IR."""
    base: list[dict] = []
    joined: list[dict] = []
    for index in range(1, ENTERPRISES + 1):
        enterprise, person = enterprise_id(index), person_id(index)
        base.append(
            {"enterprise_id": enterprise, "person_id": person, "relation_type": "controller"}
        )
        targets: list[str] = []
        for offset in range(1, index % 5 + 1):
            target = enterprise_id((index + offset - 1) % ENTERPRISES + 1)
            targets.append(target)
            joined.append(
                {
                    "person_id": person,
                    "related_enterprise_id": target,
                    "relation_type": "shareholder",
                }
            )
        if index % 4 == 0:
            base.append(
                {
                    "enterprise_id": enterprise,
                    "person_id": hub_person_id(index),
                    "relation_type": "shareholder",
                }
            )
            joined.append(
                {
                    "person_id": hub_person_id(index),
                    "related_enterprise_id": enterprise_id(index % ENTERPRISES + 1),
                    "relation_type": "shareholder",
                }
            )
        if index % 7 == 0:
            joined.append(
                {
                    "person_id": person,
                    "related_enterprise_id": enterprise,
                    "relation_type": "controller",
                }
            )
        if index % 3 == 0 and targets:
            # A duplicate relation row: same enterprise, same person, same target.
            joined.append(
                {
                    "person_id": person,
                    "related_enterprise_id": targets[0],
                    "relation_type": "shareholder",
                }
            )
    return {BASE_TABLE: base, JOINED_TABLE: joined}


def labeled_rows() -> list[dict]:
    """One labeled row per enterprise: noisy, synthetic, deliberately imperfect.

    Keyed by the scenario's declared entity field (``enterprise_id``), so the
    evaluation dataset joins to the metric result on the same identifier. High
    counts are usually risky and low counts are usually safe, but the fixture
    keeps both kinds of contradiction so no configuration can produce a perfect
    separation. The labels are fabricated and carry no business meaning.
    """
    rows = []
    for index in range(1, ENTERPRISES + 1):
        count = index % 5
        if count == 0 and index % 4 == 0:
            count = 1
        bad = (
            (count >= 3 and index % 3 != 0)
            or (count <= 1 and index % 11 == 0)
            or (count == 2 and index % 17 == 0)
        )
        rows.append(
            {
                "enterprise_id": enterprise_id(index),
                "bad_flag": 1 if bad else 0,
                "dt": RELATION_PARTITION,
            }
        )
    rows.append({"enterprise_id": ISOLATED_ENTERPRISE, "bad_flag": 0, "dt": RELATION_PARTITION})
    return rows


def relation_fixture() -> RelationFixture:
    return RelationFixture(tables=relation_tables(), labeled_rows=labeled_rows())
