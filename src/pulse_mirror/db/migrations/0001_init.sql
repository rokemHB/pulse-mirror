-- 0001: initial schema. See PLAN.md section 6.
-- Conventions: timestamps are ISO 8601 UTC strings ("...Z"); daily values are
-- keyed by the local calendar date "YYYY-MM-DD"; every measurement has a source.

-- metadata ------------------------------------------------------------------

CREATE TABLE sources (
    key  TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

INSERT INTO sources (key, name) VALUES
    ('garmin', 'Garmin Connect'),
    ('manual', 'Manual entry'),
    ('app',    'Mobile app'),
    ('demo',   'Synthetic demo data');

CREATE TABLE metric_types (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    unit        TEXT,                 -- NULL for unitless scores
    kind        TEXT NOT NULL DEFAULT 'number' CHECK (kind IN ('number', 'text')),
    category    TEXT NOT NULL,        -- grouping for the UI: heart, sleep, stress, ...
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

INSERT INTO metric_types (key, name, unit, kind, category, sort_order) VALUES
    ('resting_hr',              'Resting heart rate',        'bpm',   'number', 'heart',    10),
    ('hrv_overnight_avg',       'HRV (overnight avg)',       'ms',    'number', 'hrv',      20),
    ('hrv_overnight_5min_high', 'HRV (5-min high)',          'ms',    'number', 'hrv',      21),
    ('hrv_weekly_avg',          'HRV (7-day avg)',           'ms',    'number', 'hrv',      22),
    ('hrv_baseline_low',        'HRV baseline (low)',        'ms',    'number', 'hrv',      23),
    ('hrv_baseline_high',       'HRV baseline (high)',       'ms',    'number', 'hrv',      24),
    ('hrv_status',              'HRV status',                NULL,    'text',   'hrv',      25),
    ('sleep_duration',          'Sleep duration',            's',     'number', 'sleep',    30),
    ('sleep_deep',              'Deep sleep',                's',     'number', 'sleep',    31),
    ('sleep_light',             'Light sleep',               's',     'number', 'sleep',    32),
    ('sleep_rem',               'REM sleep',                 's',     'number', 'sleep',    33),
    ('sleep_awake',             'Awake during sleep',        's',     'number', 'sleep',    34),
    ('sleep_score',             'Sleep score',               NULL,    'number', 'sleep',    35),
    ('body_battery_max',        'Body Battery (high)',       NULL,    'number', 'energy',   40),
    ('body_battery_min',        'Body Battery (low)',        NULL,    'number', 'energy',   41),
    ('body_battery_charged',    'Body Battery charged',      NULL,    'number', 'energy',   42),
    ('body_battery_drained',    'Body Battery drained',      NULL,    'number', 'energy',   43),
    ('stress_avg',              'Stress (avg)',              NULL,    'number', 'stress',   50),
    ('stress_max',              'Stress (max)',              NULL,    'number', 'stress',   51),
    ('stress_rest_duration',    'Rest time',                 's',     'number', 'stress',   52),
    ('stress_low_duration',     'Low stress time',           's',     'number', 'stress',   53),
    ('stress_medium_duration',  'Medium stress time',        's',     'number', 'stress',   54),
    ('stress_high_duration',    'High stress time',          's',     'number', 'stress',   55),
    ('vo2max_running',          'VO2max (running)',          'ml/kg/min', 'number', 'fitness', 60),
    ('vo2max_cycling',          'VO2max (cycling)',          'ml/kg/min', 'number', 'fitness', 61),
    ('training_load_acute',     'Acute training load',       NULL,    'number', 'training', 70),
    ('training_load_chronic',   'Chronic training load',     NULL,    'number', 'training', 71),
    ('training_acwr',           'Acute:chronic load ratio',  NULL,    'number', 'training', 72),
    ('training_status',         'Training status',           NULL,    'text',   'training', 73),
    ('steps',                   'Steps',                     NULL,    'number', 'activity', 80);

-- measurements ----------------------------------------------------------------

CREATE TABLE daily_metrics (
    date       TEXT NOT NULL,                              -- local date YYYY-MM-DD
    metric_key TEXT NOT NULL REFERENCES metric_types(key),
    source     TEXT NOT NULL REFERENCES sources(key),
    value_num  REAL,
    value_text TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date, metric_key, source),
    CHECK (value_num IS NOT NULL OR value_text IS NOT NULL)
) WITHOUT ROWID;

CREATE INDEX daily_metrics_metric_date ON daily_metrics (metric_key, date);

CREATE TABLE activities (
    id               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL REFERENCES sources(key),
    source_id        TEXT NOT NULL,
    start_time       TEXT NOT NULL,                        -- UTC
    local_date       TEXT NOT NULL,
    type             TEXT NOT NULL,
    name             TEXT,
    duration_s       REAL,
    distance_m       REAL,
    elevation_gain_m REAL,
    avg_hr           REAL,
    max_hr           REAL,
    avg_power        REAL,
    norm_power       REAL,
    calories         REAL,
    training_load    REAL,
    aerobic_te       REAL,
    anaerobic_te     REAL,
    updated_at       TEXT NOT NULL,
    UNIQUE (source, source_id)
);

CREATE INDEX activities_local_date ON activities (local_date);

-- Raw responses are stored before mapping so the mapping can be re-run over
-- the whole history without fetching again. Lives on the NAS only.
CREATE TABLE raw_responses (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL REFERENCES sources(key),
    endpoint       TEXT NOT NULL,
    key            TEXT NOT NULL,                          -- a date, or "start..end"
    fetched_at     TEXT NOT NULL,
    payload_json   TEXT,                                   -- NULL = "no data"
    mapper_version INTEGER,                                -- NULL = not mapped yet
    UNIQUE (source, endpoint, key)
);

-- sync bookkeeping ------------------------------------------------------------

CREATE TABLE sync_runs (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL REFERENCES sources(key),
    kind          TEXT NOT NULL CHECK (kind IN ('scheduled', 'manual', 'backfill', 'cli')),
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error', 'auth_required', 'rate_limited', 'aborted')),
    days_fetched  INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX sync_runs_source_started ON sync_runs (source, started_at);

CREATE TABLE backfill_state (
    source        TEXT PRIMARY KEY REFERENCES sources(key),
    cursor_date   TEXT,          -- oldest date fully fetched so far
    newest_date   TEXT,          -- the date the backfill started from
    empty_streak  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL CHECK (status IN ('running', 'paused', 'done', 'error')),
    updated_at    TEXT NOT NULL
);

-- events / annotations ----------------------------------------------------------

CREATE TABLE event_categories (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    icon        TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT
);

INSERT INTO event_categories (key, name, color, icon, sort_order) VALUES
    ('trip',        'Trip / vacation', '#2a9d8f', 'plane',     10),
    ('illness',     'Illness',         '#e76f51', 'thermometer', 20),
    ('alcohol',     'Alcohol',         '#9b5de5', 'glass',     30),
    ('poor_sleep',  'Poor sleep',      '#264653', 'moon',      40),
    ('stress',      'Stress',          '#f4a261', 'bolt',      50),
    ('injury',      'Injury',          '#d62828', 'bandage',   60),
    ('vaccination', 'Vaccination',     '#457b9d', 'syringe',   70),
    ('other',       'Other',           '#6c757d', 'tag',       99);

CREATE TABLE events (
    id          TEXT PRIMARY KEY,                          -- UUID (client may generate)
    category_id INTEGER NOT NULL REFERENCES event_categories(id),
    title       TEXT NOT NULL,
    note        TEXT,
    start_at    TEXT NOT NULL,                             -- UTC
    end_at      TEXT,                                      -- NULL = single point in time
    source      TEXT NOT NULL REFERENCES sources(key),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT,
    CHECK (end_at IS NULL OR end_at >= start_at)
);

CREATE INDEX events_start ON events (start_at);

-- wellbeing scales & ratings ----------------------------------------------------
-- A scale's meaning (min/max/step) lives in scale_versions; changing it creates
-- a new version so old ratings stay interpretable.

CREATE TABLE scales (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE scale_versions (
    id          INTEGER PRIMARY KEY,
    scale_id    INTEGER NOT NULL REFERENCES scales(id),
    version     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    min_value   REAL NOT NULL,
    max_value   REAL NOT NULL,
    step        REAL NOT NULL DEFAULT 1,
    min_label   TEXT,
    max_label   TEXT,
    color       TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (scale_id, version),
    CHECK (max_value > min_value AND step > 0)
);

-- auth: per-device API tokens (only a hash is stored)
CREATE TABLE api_tokens (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    prefix       TEXT NOT NULL,                            -- first chars, for display
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE ratings (
    id               TEXT PRIMARY KEY,                     -- client-generated UUID
    scale_version_id INTEGER NOT NULL REFERENCES scale_versions(id),
    value            REAL NOT NULL,
    note             TEXT,
    recorded_at      TEXT NOT NULL,                        -- UTC
    source           TEXT NOT NULL REFERENCES sources(key),
    device_id        INTEGER REFERENCES api_tokens(id),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    deleted_at       TEXT
);

CREATE INDEX ratings_recorded ON ratings (recorded_at);
