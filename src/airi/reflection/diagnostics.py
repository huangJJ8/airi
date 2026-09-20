"""All findings are deterministic, versioned research advisories, not admission rules."""

from itertools import combinations

from airi.reflection.models import DiagnosticFinding, EvidenceReference


def evidence_references(evidence):
    refs = []
    for item in evidence:
        prefix = f"experiment:{item.experiment_run_id}"
        for field in ("evaluation", "join_summary", "warnings", "synthetic_data"):
            refs.append(
                EvidenceReference(
                    ref=f"{prefix}:{field}", experiment_run_id=item.experiment_run_id, field=field
                )
            )
        for i, _ in enumerate(item.evaluation.threshold_candidates):
            refs.append(
                EvidenceReference(
                    ref=f"{prefix}:candidate:{i}",
                    experiment_run_id=item.experiment_run_id,
                    field=f"evaluation.threshold_candidates.{i}",
                )
            )
    return refs


class ReflectionDiagnostics:
    def analyze(self, evidence, policy):
        findings = []
        bands = policy.diagnostic_bands
        for item in evidence:
            e = item.evaluation
            prefix = f"experiment:{item.experiment_run_id}"

            def add(kind, summary, *, warning=False, pattern=None, refs=None):
                findings.append(
                    DiagnosticFinding(
                        finding_id=f"finding:{item.experiment_run_id}:{kind}",
                        finding_type=kind,
                        severity="warning" if warning else "info",
                        experiment_run_ids=[item.experiment_run_id],
                        evidence_refs=refs or [f"{prefix}:evaluation"],
                        summary=summary,
                        pattern=pattern,
                    )
                )

            if e.coverage is not None and e.coverage < 1:
                add(
                    "coverage_gap",
                    f"{e.null_metric}/{e.labeled_sample} labeled entities lack a "
                    f"non-null metric (missing ratio {1 - e.coverage:.6f}); investigate causes.",
                    refs=[f"{prefix}:evaluation", f"{prefix}:join_summary"],
                )
            if e.coverage is not None and e.coverage < bands.coverage_warning_below:
                add(
                    "high_null_rate",
                    "Coverage is below the policy research advisory band.",
                    warning=True,
                )
            if e.ks.value is not None:
                if e.ks.value < bands.ks_review_below:
                    add(
                        "weak_separation",
                        f"KS={e.ks.value}; below the policy review band.",
                        warning=True,
                    )
                elif e.ks.value >= bands.ks_strong_at_least:
                    gap = e.coverage is not None and e.coverage < bands.coverage_warning_below
                    add(
                        "strong_separation",
                        f"KS={e.ks.value}; above the policy research band. "
                        "This does not establish business value or offset missing coverage.",
                        pattern="strong_separation_with_coverage_gap" if gap else None,
                    )
            if e.ks.direction == "undetermined":
                add(
                    "risk_direction_uncertain",
                    "Persisted risk direction is undetermined.",
                    warning=True,
                )
            if e.labeled_sample < bands.small_sample_below:
                add(
                    "small_sample",
                    f"Labeled sample={e.labeled_sample}; below the policy research sample band.",
                    warning=True,
                )
            if not e.good_count or not e.bad_count:
                add(
                    "single_class",
                    "One label class is absent; separation evidence unavailable.",
                    warning=True,
                )
            if e.actual_bins < bands.few_bins_below:
                add(
                    "few_effective_bins",
                    f"Effective numeric bins={e.actual_bins}; repeated values "
                    "or limited support may reduce resolution.",
                    warning=True,
                )
            pure = sum(b.good == 0 or b.bad == 0 for b in e.bins)
            if (
                e.iv is not None
                and e.iv > bands.iv_sensitivity_above
                and pure
                and e.labeled_sample < bands.small_sample_below
                and e.epsilon > 0
            ):
                add(
                    "high_iv_sensitivity",
                    f"IV={e.iv}, pure bins={pure}, "
                    f"sample={e.labeled_sample}, epsilon={e.epsilon}; potential smoothing "
                    "sensitivity, not proof of overfitting.",
                    warning=True,
                )
            tradeoffs = []
            for (i, a), (j, b) in combinations(enumerate(e.threshold_candidates), 2):
                if all(v is not None for v in (a.precision, b.precision, a.recall, b.recall)):
                    if (a.precision - b.precision) * (a.recall - b.recall) < 0:
                        tradeoffs.extend([f"{prefix}:candidate:{i}", f"{prefix}:candidate:{j}"])
            if tradeoffs:
                add(
                    "threshold_tradeoff",
                    "Existing candidates trade higher precision against "
                    "lower recall. Review hit rate and lift; no candidate is selected.",
                    refs=list(dict.fromkeys(tradeoffs)),
                )
            add(
                "insufficient_evidence",
                "This experiment alone does not prove temporal stability, "
                "out-of-time performance or confirmed business causality.",
                warning=True,
                refs=[f"{prefix}:warnings", f"{prefix}:synthetic_data"],
            )
        return findings

    def compare(self, evidence):
        findings = []
        for a, b in combinations(evidence, 2):
            av, bv = a.evaluation, b.evaluation
            findings.append(
                DiagnosticFinding(
                    finding_id=f"finding:comparison:{a.experiment_run_id}:{b.experiment_run_id}",
                    finding_type="cross_metric_difference",
                    severity="info",
                    experiment_run_ids=[a.experiment_run_id, b.experiment_run_id],
                    evidence_refs=[f"experiment:{x.experiment_run_id}:evaluation" for x in (a, b)],
                    summary=f"{av.metric_name}: coverage={av.coverage}, "
                    f"KS={av.ks.value}, IV={av.iv}; "
                    f"{bv.metric_name}: coverage={bv.coverage}, KS={bv.ks.value}, IV={bv.iv}. "
                    "Comparable recorded conditions; no winner or business ranking assigned.",
                )
            )
        return findings
