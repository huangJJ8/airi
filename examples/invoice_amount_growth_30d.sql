WITH current_period AS (
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-08-10'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`
),
previous_period AS (
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-07-11'
    AND `invoice_date` < DATE '2026-08-10'
GROUP BY `seller_tax_no`
)
SELECT
    COALESCE(c.entity_id, p.entity_id) AS entity_id,
    COALESCE(c.invoice_amount_30d, 0) AS current_amount,
    COALESCE(p.invoice_amount_30d, 0) AS previous_amount,
    CASE WHEN COALESCE(p.invoice_amount_30d, 0) = 0 THEN NULL
         ELSE 1.0 * (COALESCE(c.invoice_amount_30d, 0) - COALESCE(p.invoice_amount_30d, 0))
              / COALESCE(p.invoice_amount_30d, 0)
    END AS `invoice_amount_growth_30d`
FROM current_period c
FULL OUTER JOIN previous_period p ON c.entity_id <=> p.entity_id;
