-- Operational recovery metadata; does not change DM-021..025 canonical columns.
CREATE TABLE original_actions (
 action_id TEXT PRIMARY KEY,
 material_action_id TEXT NOT NULL REFERENCES material_actions(material_action_id),
 business_key TEXT NOT NULL,
 material_sha256 TEXT NOT NULL,
 operation_class TEXT NOT NULL CHECK(operation_class IN ('ROUTINE_ORDER_STATUS','SANITIZED_INTERNAL_ESCALATION')),
 episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 fixture_epoch TEXT NOT NULL,
 database_uuid TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('REGISTERED','DISPATCHED','CLOSED_NOT_COMMITTED')),
 registered_at TEXT NOT NULL,
 last_writer_id TEXT,
 dispatched_at TEXT,
 closed_at TEXT,
 CHECK ((state='REGISTERED' AND dispatched_at IS NULL AND closed_at IS NULL)
     OR (state='DISPATCHED' AND last_writer_id IS NOT NULL AND dispatched_at IS NOT NULL AND closed_at IS NULL)
     OR (state='CLOSED_NOT_COMMITTED' AND closed_at IS NOT NULL)),
 UNIQUE(action_id,business_key,material_sha256,material_action_id));
CREATE INDEX idx_original_actions_business ON original_actions(business_key);
CREATE TABLE reconciliation_events (
 reconciliation_id TEXT PRIMARY KEY,
 action_id TEXT NOT NULL REFERENCES original_actions(action_id),
 outcome TEXT NOT NULL CHECK(outcome='RECONCILED_NOT_COMMITTED'),
 database_uuid TEXT NOT NULL,
 fixture_epoch TEXT NOT NULL,
 reconciled_at TEXT NOT NULL,
 details_json TEXT NOT NULL CHECK(json_valid(details_json)),
 UNIQUE(action_id,outcome));
CREATE TRIGGER queue_effects_requires_dispatch BEFORE INSERT ON queue_effects
 WHEN NOT EXISTS (
  SELECT 1 FROM original_actions a WHERE a.action_id=NEW.action_id
   AND a.business_key=NEW.business_key AND a.material_action_id=NEW.material_action_id
   AND a.material_sha256=NEW.material_sha256 AND a.state='DISPATCHED'
   AND a.database_uuid=(SELECT identity_uuid FROM database_identity WHERE singleton=1)
   AND NOT EXISTS(SELECT 1 FROM original_actions closed
                  WHERE closed.business_key=NEW.business_key AND closed.state='CLOSED_NOT_COMMITTED'))
 BEGIN SELECT RAISE(ABORT,'original action not dispatched or business fenced'); END;
CREATE TRIGGER original_actions_no_delete BEFORE DELETE ON original_actions
 BEGIN SELECT RAISE(ABORT,'original action history is immutable'); END;
CREATE TRIGGER original_actions_identity_update BEFORE UPDATE ON original_actions
 WHEN OLD.action_id<>NEW.action_id OR OLD.material_action_id<>NEW.material_action_id
  OR OLD.business_key<>NEW.business_key OR OLD.material_sha256<>NEW.material_sha256
  OR OLD.operation_class<>NEW.operation_class OR OLD.episode_id<>NEW.episode_id
  OR OLD.fixture_epoch<>NEW.fixture_epoch OR OLD.database_uuid<>NEW.database_uuid
 BEGIN SELECT RAISE(ABORT,'original action identity is immutable'); END;
CREATE TRIGGER original_actions_no_reopen BEFORE UPDATE ON original_actions
 WHEN (OLD.state='CLOSED_NOT_COMMITTED' AND NEW.state<>'CLOSED_NOT_COMMITTED')
   OR (OLD.state='DISPATCHED' AND NEW.state='REGISTERED')
 BEGIN SELECT RAISE(ABORT,'original action lifecycle cannot reopen'); END;
CREATE TRIGGER reconciliation_events_no_update BEFORE UPDATE ON reconciliation_events
 BEGIN SELECT RAISE(ABORT,'immutable reconciliation event'); END;
CREATE TRIGGER reconciliation_events_no_delete BEFORE DELETE ON reconciliation_events
 BEGIN SELECT RAISE(ABORT,'immutable reconciliation event'); END;
