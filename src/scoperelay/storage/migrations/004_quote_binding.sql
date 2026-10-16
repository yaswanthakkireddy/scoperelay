-- ADR-019: backfill historical episodes as CandidateRequestV1.
-- The application must pin every new episode before inference. Never change a pin.
CREATE TABLE episode_candidate_protocols (
 episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id),
 protocol_version TEXT NOT NULL CHECK(protocol_version IN ('V1_SPANS','V2_EXACT_QUOTE')),
 pinned_at TEXT NOT NULL);
-- Preserve interpretation of every episode already present before migration.
INSERT INTO episode_candidate_protocols(episode_id,protocol_version,pinned_at)
SELECT episode_id,'V1_SPANS',started_at FROM episodes;
CREATE TRIGGER episode_protocol_no_update BEFORE UPDATE ON episode_candidate_protocols
BEGIN SELECT RAISE(ABORT,'candidate protocol pin is immutable'); END;
CREATE TRIGGER episode_protocol_no_delete BEFORE DELETE ON episode_candidate_protocols
BEGIN SELECT RAISE(ABORT,'candidate protocol pin is immutable'); END;
CREATE TRIGGER episode_protocol_before_invocation BEFORE INSERT ON episode_candidate_protocols
WHEN EXISTS(SELECT 1 FROM agent_invocations WHERE episode_id=NEW.episode_id)
BEGIN SELECT RAISE(ABORT,'cannot pin after agent invocation'); END;
CREATE TABLE candidate_quote_bindings (
 binding_id TEXT PRIMARY KEY,
 episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 invocation_id TEXT NOT NULL REFERENCES agent_invocations(invocation_id),
 tool_event_id TEXT NOT NULL REFERENCES tool_events(tool_event_id),
 protocol_version TEXT NOT NULL CHECK(protocol_version='V2_EXACT_QUOTE'),
 model_quotes_json TEXT NOT NULL CHECK(json_valid(model_quotes_json)),
 bound_spans_json TEXT NOT NULL CHECK(json_valid(bound_spans_json)),
 model_source_id TEXT NOT NULL,
 model_source_version INTEGER NOT NULL,
 model_source_digest TEXT NOT NULL,
 binder_version TEXT NOT NULL CHECK(binder_version='EXACT_UNIQUE_V1'),
 binding_status TEXT NOT NULL CHECK(binding_status IN ('VALIDATED','REJECTED')),
 relevance_status TEXT NOT NULL CHECK(relevance_status IN ('PASSED','FAILED','NOT_RUN')),
 diagnostic_code TEXT,
 candidate_id TEXT REFERENCES candidate_requests(candidate_id),
 created_at TEXT NOT NULL);
CREATE INDEX candidate_quote_bindings_episode_idx ON candidate_quote_bindings(episode_id,invocation_id);
CREATE TRIGGER candidate_quote_bindings_no_update BEFORE UPDATE ON candidate_quote_bindings
BEGIN SELECT RAISE(ABORT,'quote binding evidence is append-only'); END;
CREATE TRIGGER candidate_quote_bindings_no_delete BEFORE DELETE ON candidate_quote_bindings
BEGIN SELECT RAISE(ABORT,'quote binding evidence is append-only'); END;
