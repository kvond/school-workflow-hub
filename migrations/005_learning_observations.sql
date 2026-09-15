-- Private reviewed learning history in the existing state database.
CREATE TABLE learning_observations (
    observation_id TEXT PRIMARY KEY,
    class_key TEXT NOT NULL,
    learner_key TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance IN ('source_read','synthetic')),
    payload_json TEXT NOT NULL
);
CREATE INDEX learning_observations_scope ON learning_observations(class_key, observed_date, provenance);
