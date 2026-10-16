-- DM-021: a frozen material row is insert-only, including its identity and digest.
CREATE TRIGGER material_actions_no_update BEFORE UPDATE ON material_actions
 BEGIN SELECT RAISE(ABORT,'immutable material action'); END;
CREATE TRIGGER material_actions_no_delete BEFORE DELETE ON material_actions
 BEGIN SELECT RAISE(ABORT,'immutable material action'); END;
CREATE TABLE database_identity (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 identity_uuid TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL);
CREATE TRIGGER database_identity_no_update BEFORE UPDATE ON database_identity
 BEGIN SELECT RAISE(ABORT,'immutable database identity'); END;
CREATE TRIGGER database_identity_no_delete BEFORE DELETE ON database_identity
 BEGIN SELECT RAISE(ABORT,'immutable database identity'); END;
