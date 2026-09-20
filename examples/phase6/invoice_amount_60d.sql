SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_60d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-07-11'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`;
