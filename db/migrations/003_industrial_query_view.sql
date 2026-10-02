-- One row per measured sample; normalization never changes source values.
CREATE OR REPLACE VIEW demo_industrial.measurement_observations AS
SELECT m.measurement_id,b.batch_id,b.part_id,b.produced_at,m.operation_no,
       m.metric,m.value,m.unit,m.measured_at,m.method,
       CASE WHEN m.metric='holding_temperature' AND m.unit='degC' THEN m.value
            WHEN m.metric='holding_temperature' AND m.unit='K' THEN m.value-273.15
            ELSE NULL::numeric END AS temperature_c
FROM demo_industrial.process_measurements m
JOIN demo_industrial.production_batches b ON m.batch_id=b.batch_id;
COMMENT ON VIEW demo_industrial.measurement_observations IS
'industrial-query-v1: synthetic; one measurement per row; production-time scope; unknown temperature units NULL; only measured batches';
GRANT SELECT ON demo_industrial.measurement_observations TO readonly_user;
