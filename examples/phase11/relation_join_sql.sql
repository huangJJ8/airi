SELECT
    ep.`enterprise_id` AS entity_id,
    COUNT(DISTINCT pe.`related_enterprise_id`) AS `related_enterprise_count`
FROM `demo`.`enterprise_person_relation` AS ep
INNER JOIN `demo`.`person_enterprise_relation` AS pe
    ON ep.`person_id` = pe.`person_id`
WHERE pe.`related_enterprise_id` <> ep.`enterprise_id`

GROUP BY ep.`enterprise_id`;

