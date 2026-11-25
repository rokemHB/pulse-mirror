# Garmin test fixtures

- `synthetic/`: hand-written payloads in the shape of the real responses (made-up values).
- `recorded/`: real responses, **anonymized** by `pulse-mirror dev anonymize` (ids replaced, names and
  GPS dropped, dates shifted to 2025-01-15, numbers perturbed by ±10 %). Reviewed before committing.
- `_raw/`: un-anonymized recordings. **Git-ignored. Never commit.**

All files are named after the adapter endpoint (`user_summary`, `sleep`, `hrv`, `max_metrics`,
`training_status`, `activities`).
