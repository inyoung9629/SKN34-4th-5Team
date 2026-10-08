-- Version 1. Separate namespace; never writes service Place or RAG tables.
CREATE SCHEMA IF NOT EXISTS place_staging;

CREATE TABLE IF NOT EXISTS place_staging.snapshots (
    snapshot_id text PRIMARY KEY,
    manifest_sha256 text NOT NULL,
    manifest jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS place_staging.source_runs (
    snapshot_id text NOT NULL REFERENCES place_staging.snapshots(snapshot_id),
    stadium_code text NOT NULL,
    source text NOT NULL CHECK (source IN ('SBIZ', 'PARK', 'TOUR_WALK', 'GOOGLE')),
    collected_at timestamptz NOT NULL,
    reference_month text,
    metadata jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, stadium_code, source)
);

CREATE TABLE IF NOT EXISTS place_staging.public_places (
    snapshot_id text NOT NULL,
    stadium_code text NOT NULL,
    source text NOT NULL CHECK (source IN ('SBIZ', 'PARK', 'TOUR')),
    source_run text NOT NULL,
    source_id text NOT NULL,
    selection_status text NOT NULL CHECK (selection_status IN ('selected', 'needs_review')),
    name text NOT NULL,
    address text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('restaurant', 'bar', 'cafe', 'convenience_store', 'play_facility', 'walk_candidate')),
    lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng double precision NOT NULL CHECK (lng BETWEEN -180 AND 180),
    distance_m double precision NOT NULL CHECK (distance_m BETWEEN 0 AND 2500),
    category_large text,
    category_middle text,
    category_small text,
    verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status = 'unverified'),
    payload jsonb NOT NULL,
    PRIMARY KEY (snapshot_id, stadium_code, source, source_id),
    FOREIGN KEY (snapshot_id, stadium_code, source_run)
        REFERENCES place_staging.source_runs(snapshot_id, stadium_code, source),
    CHECK (source_run = CASE source WHEN 'TOUR' THEN 'TOUR_WALK' ELSE source END)
);
CREATE INDEX IF NOT EXISTS public_places_filter_idx
    ON place_staging.public_places(snapshot_id, stadium_code, selection_status, kind);

CREATE TABLE IF NOT EXISTS place_staging.google_lodging_ids (
    snapshot_id text NOT NULL,
    stadium_code text NOT NULL,
    source_run text NOT NULL DEFAULT 'GOOGLE' CHECK (source_run = 'GOOGLE'),
    place_id text NOT NULL CHECK (length(place_id) > 0),
    PRIMARY KEY (snapshot_id, stadium_code, place_id),
    FOREIGN KEY (snapshot_id, stadium_code, source_run)
        REFERENCES place_staging.source_runs(snapshot_id, stadium_code, source)
);
