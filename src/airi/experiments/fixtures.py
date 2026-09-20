"""Fixed-seed synthetic data. No production labels or claim of real-world effectiveness."""

import random


def experiment_fixture():
    randomizer = random.Random(20260911)
    source, dataset = [], []
    for index in range(200):
        entity = f"S{index:04d}"
        amount = randomizer.randint(0, 1000)
        label = int(amount + randomizer.randint(-500, 500) > 650)
        dataset.append(
            {
                "seller_tax_no": entity,
                "bad_flag": None if index % 17 == 0 else label,
                "dt": "20260909",
            }
        )
        if index % 13 == 0:
            continue
        count = randomizer.randint(1, 5)
        for _ in range(count):
            source.append(
                {
                    "seller_tax_no": entity,
                    "invoice_date": "2026-08-20",
                    "invoice_amt": str(amount // count),
                    "dt": "20260820",
                }
            )
        source.append(
            {
                "seller_tax_no": entity,
                "invoice_date": "2026-07-20",
                "invoice_amt": "0" if index % 9 == 0 else str(randomizer.randint(1, 1000)),
                "dt": "20260720",
            }
        )
    source.append(
        {
            "seller_tax_no": "METRIC_ONLY",
            "invoice_date": "2026-08-20",
            "invoice_amt": "10",
            "dt": "20260820",
        }
    )
    return source, dataset
