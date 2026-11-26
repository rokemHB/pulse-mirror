-- Garmin's "generic" VO2max (stored as vo2max_running) is the general estimate
-- from runs and walks; the key stays for API stability, only the label changes.
UPDATE metric_types SET name = 'VO2max (general)' WHERE key = 'vo2max_running';
