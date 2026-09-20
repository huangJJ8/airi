from airi.temporal.statistics import direction_consistency, summary


class TemporalDiagnostics:
    def assess(self, matrix, reference_id, oot_id, stability, promotion, evaluation):
        reference = next(item for item in matrix if item["time_slice_id"] == reference_id)
        oot = next((item for item in matrix if item["time_slice_id"] == oot_id), None)
        findings = []
        aggregates = {}
        for role in ("baseline", "candidate"):
            aggregates[role] = {
                key: summary([item[role][key] for item in matrix])
                for key in ("coverage", "ks", "iv")
            }
            aggregates[role]["risk_direction"] = direction_consistency(
                reference[role]["direction"], [item[role]["direction"] for item in matrix]
            )
        for item in matrix:
            value, base = item["candidate"], reference["candidate"]

            def finding(kind):
                findings.append({"type": kind, "time_slice_id": item["time_slice_id"]})

            if (
                value["ks"] is None
                or value["labeled_sample"] < evaluation.min_labeled_samples
                or value["direction"] == "undetermined"
            ):
                finding("insufficient_temporal_evidence")
            for key, margin, kind in (
                ("coverage", stability.coverage_material_drop, "coverage_drift"),
                ("ks", stability.ks_material_drop, "ks_degradation"),
            ):
                if (
                    value[key] is not None
                    and base[key] is not None
                    and value[key] < base[key] - margin
                ):
                    finding(kind)
            if (
                value["iv"] is not None
                and base["iv"] is not None
                and abs(value["iv"] - base["iv"]) > stability.iv_material_change
            ):
                finding("iv_instability")
            if base["direction"] != "undetermined" and value["direction"] not in (
                base["direction"],
                "undetermined",
            ):
                finding("risk_direction_flip")
            if value["psi"]["value"] is None:
                finding("insufficient_temporal_evidence")
            elif value["psi"]["value"] > stability.psi_review_above:
                finding("population_shift")
            if not value["frozen_thresholds"]:
                finding("insufficient_temporal_evidence")
            for threshold in value["frozen_thresholds"]:
                for key, margin in (
                    ("precision", stability.threshold_precision_drop),
                    ("recall", stability.threshold_recall_drop),
                    ("lift", stability.threshold_lift_drop),
                ):
                    change = threshold["deltas"][key]
                    if change is None:
                        finding("insufficient_temporal_evidence")
                    elif change < -margin:
                        finding("threshold_degradation")
            if item["time_slice_id"] == oot_id and item["comparison"]["outcome"] == "worse":
                finding("oot_degradation")
        if oot is None or len(matrix) - 1 < stability.min_historical_slices:
            findings.append({"type": "insufficient_temporal_evidence", "time_slice_id": oot_id})
        kinds = {item["type"] for item in findings}
        not_worse = sum(
            item["comparison"]["coverage_delta"] is not None
            and item["comparison"]["ks_delta"] is not None
            and item["comparison"]["coverage_delta"] >= -stability.coverage_material_drop
            and item["comparison"]["ks_delta"] >= -stability.ks_material_drop
            for item in matrix
        ) / len(matrix)
        eligibility = "eligible_for_review"
        if "risk_direction_flip" in kinds:
            eligibility = "not_eligible"
        elif kinds - {"iv_instability"} or not_worse < promotion.min_not_materially_worse_fraction:
            eligibility = "need_more_evidence"
        status = (
            "inconclusive"
            if "insufficient_temporal_evidence" in kinds
            else "failed"
            if eligibility != "eligible_for_review"
            else "passed_with_warnings"
            if findings
            else "passed"
        )
        return aggregates, findings, eligibility, status
