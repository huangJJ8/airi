"""Seeded synthetic samples, deliberately separated anchors to avoid overlapping windows."""

from datetime import datetime, timedelta
from random import Random


def temporal_fixture(*, stable=False, mode=None):
    """mode: "stable" | "direction_flip" | "oot_degradation"; `stable` is the legacy selector.

    Only the OOT slice differs between modes, so historical slices stay comparable:
    the 30d baseline never separates and the 60d candidate separates perfectly.
    """
    mode = mode or ("stable" if stable else "direction_flip")
    source, datasets, slices = [], [], []
    for index in range(4):
        anchor = datetime.fromisoformat("2026-10-01T00:00:00+08:00") + timedelta(days=index * 100)
        rows = []
        random = Random(5100)  # Repeat distributions; only OOT changes in unstable fixtures.
        for entity in range(200):
            key = f"T{entity:04d}"
            bad = entity % 2
            if mode == "oot_degradation" and index == 3:
                # Separation moves into the 30d baseline; unrelated older invoices dilute the
                # 60d candidate. Direction stays higher_is_riskier, so no flip is reported.
                current = bad * 1500 + random.randrange(100, 300)
                previous = random.randrange(0, 1600)
            else:
                current = random.randrange(100, 1100)
                previous = bad * 1500 + random.randrange(100, 300)
                if mode == "direction_flip" and index == 3:
                    previous = (1 - bad) * 2500 + random.randrange(100, 300)
            for offset, amount in ((10, current), (45, previous)):
                date = anchor - timedelta(days=offset)
                source.append(
                    {
                        "seller_tax_no": key,
                        "invoice_date": date.date().isoformat(),
                        "invoice_amt": str(amount),
                        "dt": date.strftime("%Y%m%d"),
                    }
                )
            rows.append({"seller_tax_no": key, "bad_flag": bad, "dt": anchor.strftime("%Y%m%d")})
        datasets.extend(rows)
        slices.append((anchor, rows))
    return source, datasets, slices
