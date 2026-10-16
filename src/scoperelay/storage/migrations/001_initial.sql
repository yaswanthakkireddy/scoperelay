-- DM-001..028. Schema only: no application path may write queue_effects.
CREATE TABLE organizations (
 org_id TEXT PRIMARY KEY CHECK(length(org_id)>0), name TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(policy_version>=1),
 active INTEGER NOT NULL CHECK(active IN (0,1)), row_version INTEGER NOT NULL CHECK(row_version>=1));
CREATE TABLE principals (
 principal_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 role TEXT NOT NULL CHECK(role IN ('EVALUATOR','MANAGER','SUPERVISOR')),
 display_name TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 version INTEGER NOT NULL CHECK(version>=1), UNIQUE(principal_id,org_id));
CREATE TABLE operator_sessions (
 session_id_hash TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 role_context TEXT NOT NULL CHECK(role_context IN ('EVALUATOR','MANAGER','SUPERVISOR')),
 csrf_secret_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 revoked_at TEXT, version INTEGER NOT NULL CHECK(version>=1));
CREATE TABLE org_policy (
 org_id TEXT PRIMARY KEY REFERENCES organizations(org_id), version INTEGER NOT NULL CHECK(version>=1),
 permitted_ops_json TEXT NOT NULL CHECK(json_valid(permitted_ops_json)),
 routine_field_allowlist_json TEXT NOT NULL CHECK(json_valid(routine_field_allowlist_json)),
 internal_field_allowlist_json TEXT NOT NULL CHECK(json_valid(internal_field_allowlist_json)),
 escalation_reasons_json TEXT NOT NULL CHECK(json_valid(escalation_reasons_json)),
 internal_recipient_id TEXT NOT NULL REFERENCES contacts(contact_id), updated_at TEXT NOT NULL,
 FOREIGN KEY(internal_recipient_id,org_id) REFERENCES contacts(contact_id,org_id));
CREATE TABLE manager_grants (
 grant_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 manager_id TEXT NOT NULL REFERENCES principals(principal_id),
 operation_class TEXT NOT NULL CHECK(operation_class IN ('ROUTINE_ORDER_STATUS','SANITIZED_INTERNAL_ESCALATION')),
 scope_ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
 policy_version INTEGER NOT NULL CHECK(policy_version>=1), version INTEGER NOT NULL CHECK(version>=1),
 expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(grant_id,org_id,scope_ticket_id),
 FOREIGN KEY(manager_id,org_id) REFERENCES principals(principal_id,org_id),
 FOREIGN KEY(scope_ticket_id,org_id) REFERENCES tickets(ticket_id,org_id));
CREATE TABLE customers (
 customer_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 display_name TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 version INTEGER NOT NULL CHECK(version>=1), UNIQUE(customer_id,org_id));
CREATE TABLE accounts (
 account_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 customer_id TEXT NOT NULL REFERENCES customers(customer_id),
 active INTEGER NOT NULL CHECK(active IN (0,1)), version INTEGER NOT NULL CHECK(version>=1),
 UNIQUE(account_id,org_id), FOREIGN KEY(customer_id,org_id) REFERENCES customers(customer_id,org_id));
CREATE TABLE contacts (
 contact_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 customer_id TEXT REFERENCES customers(customer_id), principal_id TEXT REFERENCES principals(principal_id),
 channel TEXT NOT NULL CHECK(channel IN ('SYNTHETIC_CUSTOMER_QUEUE','SYNTHETIC_INTERNAL_QUEUE')),
 address TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)),
 active INTEGER NOT NULL CHECK(active IN (0,1)), version INTEGER NOT NULL CHECK(version>=1),
 UNIQUE(contact_id,org_id),
 FOREIGN KEY(customer_id,org_id) REFERENCES customers(customer_id,org_id),
 FOREIGN KEY(principal_id,org_id) REFERENCES principals(principal_id,org_id),
 CHECK ((channel='SYNTHETIC_CUSTOMER_QUEUE' AND customer_id IS NOT NULL AND principal_id IS NULL)
     OR (channel='SYNTHETIC_INTERNAL_QUEUE' AND customer_id IS NULL AND principal_id IS NOT NULL)));
CREATE TABLE orders (
 order_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 order_status TEXT NOT NULL CHECK(order_status IN ('PLACED','PROCESSING','IN_TRANSIT','DELIVERED','DELAYED')),
 customer_visible_eta TEXT, internal_notes TEXT,
 version INTEGER NOT NULL CHECK(version>=1), active INTEGER NOT NULL CHECK(active IN (0,1)),
 FOREIGN KEY(account_id,org_id) REFERENCES accounts(account_id,org_id));
CREATE TABLE tickets (
 ticket_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id),
 customer_id TEXT NOT NULL REFERENCES customers(customer_id),
 assigned_grant_id TEXT REFERENCES manager_grants(grant_id),
 current_source_version INTEGER NOT NULL CHECK(current_source_version>=1),
 assignment_version INTEGER NOT NULL CHECK(assignment_version>=1),
 active INTEGER NOT NULL CHECK(active IN (0,1)), UNIQUE(ticket_id,org_id),
 FOREIGN KEY(customer_id,org_id) REFERENCES customers(customer_id,org_id),
 FOREIGN KEY(assigned_grant_id,org_id,ticket_id) REFERENCES manager_grants(grant_id,org_id,scope_ticket_id));
CREATE TABLE ticket_sources (
 source_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
 source_version INTEGER NOT NULL CHECK(source_version>=1),
 body_text TEXT NOT NULL CHECK(length(body_text)<=8192),
 body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
 business_interaction_id TEXT NOT NULL CHECK(length(business_interaction_id)>0),
 created_at TEXT NOT NULL, UNIQUE(ticket_id,source_version));
CREATE TRIGGER ticket_sources_no_update BEFORE UPDATE ON ticket_sources
 BEGIN SELECT RAISE(ABORT,'immutable ticket source'); END;
CREATE TRIGGER ticket_sources_no_delete BEFORE DELETE ON ticket_sources
 BEGIN SELECT RAISE(ABORT,'immutable ticket source'); END;
CREATE TRIGGER ticket_sources_one_ticket_per_interaction BEFORE INSERT ON ticket_sources
 WHEN EXISTS(SELECT 1 FROM ticket_sources s JOIN tickets old_ticket ON old_ticket.ticket_id=s.ticket_id
             JOIN tickets new_ticket ON new_ticket.ticket_id=NEW.ticket_id
             WHERE s.business_interaction_id=NEW.business_interaction_id
             AND old_ticket.org_id=new_ticket.org_id AND s.ticket_id<>NEW.ticket_id)
 BEGIN SELECT RAISE(ABORT,'interaction bound to another ticket'); END;
CREATE TABLE scenarios (
 scenario_id TEXT PRIMARY KEY, fixture_version TEXT NOT NULL, fixture_epoch TEXT NOT NULL,
 name TEXT NOT NULL, manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 created_at TEXT NOT NULL);
CREATE TABLE episodes (
 episode_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
 fixture_epoch TEXT NOT NULL, ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
 source_version INTEGER NOT NULL CHECK(source_version>=1),
 grant_id TEXT NOT NULL REFERENCES manager_grants(grant_id),
 run_kind TEXT NOT NULL CHECK(run_kind IN ('LIVE','RECORDED_REPLAY','DETERMINISTIC_COMPONENT_TEST')),
 workflow_state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE agent_invocations (
 invocation_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 agent_role TEXT NOT NULL CHECK(agent_role IN ('A','B')),
 provider TEXT NOT NULL, model_id TEXT NOT NULL,
 is_live INTEGER NOT NULL CHECK(is_live IN (0,1)), status TEXT NOT NULL,
 started_at TEXT NOT NULL, ended_at TEXT, failure_code TEXT,
 turn_evidence_json TEXT NOT NULL CHECK(json_valid(turn_evidence_json)),
 UNIQUE(invocation_id,episode_id));
CREATE TABLE candidate_requests (
 candidate_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 invocation_id TEXT NOT NULL REFERENCES agent_invocations(invocation_id),
 operation_hint TEXT NOT NULL, order_ref_text TEXT, requested_recipient_text TEXT,
 content_class_hint TEXT, source_spans_json TEXT NOT NULL CHECK(json_valid(source_spans_json)),
 uncertainty_json TEXT NOT NULL CHECK(json_valid(uncertainty_json)),
 proposal_json TEXT NOT NULL CHECK(json_valid(proposal_json)));
CREATE TABLE verifications (
 verification_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 grant_id TEXT NOT NULL REFERENCES manager_grants(grant_id), ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
 customer_id TEXT REFERENCES customers(customer_id), account_id TEXT REFERENCES accounts(account_id),
 order_id TEXT REFERENCES orders(order_id), recipient_contact_id TEXT REFERENCES contacts(contact_id),
 versions_json TEXT NOT NULL CHECK(json_valid(versions_json)), result_code TEXT NOT NULL,
 observed_at TEXT NOT NULL, UNIQUE(verification_id,episode_id));
CREATE TABLE workflow_decisions (
 decision_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 verification_id TEXT REFERENCES verifications(verification_id),
 decision TEXT NOT NULL CHECK(decision IN ('ALLOW','DENY','CLARIFY','REQUEST APPROVAL','INCOMPLETE EVIDENCE')),
 reason_code TEXT NOT NULL, basis_json TEXT NOT NULL CHECK(json_valid(basis_json)),
 decided_at TEXT NOT NULL);
CREATE TABLE handoffs (
 handoff_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 h1_grant_ref_json TEXT NOT NULL CHECK(json_valid(h1_grant_ref_json)),
 h2_verified_refs_json TEXT NOT NULL CHECK(json_valid(h2_verified_refs_json)),
 h3_candidate_json TEXT NOT NULL CHECK(json_valid(h3_candidate_json)),
 h4_source_refs_json TEXT NOT NULL CHECK(json_valid(h4_source_refs_json)),
 assembled_at TEXT NOT NULL, validation_status TEXT NOT NULL,
 UNIQUE(handoff_id,episode_id));
CREATE TABLE tool_events (
 tool_event_id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL REFERENCES agent_invocations(invocation_id),
 call_id TEXT NOT NULL, tool_name TEXT NOT NULL CHECK(tool_name IN ('read_assigned_ticket','read_verified_order_status')),
 args_json TEXT NOT NULL CHECK(json_valid(args_json)),
 observation_json TEXT CHECK(observation_json IS NULL OR json_valid(observation_json)),
 observation_owner TEXT, observed_at TEXT, tool_status TEXT NOT NULL,
 handoff_id TEXT REFERENCES handoffs(handoff_id), verification_id TEXT REFERENCES verifications(verification_id),
 observation_sent_turn INTEGER CHECK(observation_sent_turn IS NULL OR observation_sent_turn>=1),
 continuation_turn INTEGER CHECK(continuation_turn IS NULL OR continuation_turn>=1),
 CHECK(continuation_turn IS NULL OR (observation_sent_turn IS NOT NULL AND continuation_turn>observation_sent_turn)),
 UNIQUE(invocation_id,call_id), UNIQUE(tool_event_id,invocation_id));
CREATE TABLE agent_b_proposals (
 proposal_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 invocation_id TEXT NOT NULL, handoff_id TEXT NOT NULL,
 purpose TEXT CHECK(purpose IS NULL OR purpose IN ('STATUS_BRIEF','STATUS_WITH_ETA','STATUS_NO_ETA')),
 template_variant TEXT CHECK(template_variant IS NULL OR template_variant IN ('STATUS_T1','STATUS_T2','STATUS_NOETA_T1')),
 selected_field_ids_json TEXT CHECK(selected_field_ids_json IS NULL OR json_valid(selected_field_ids_json)),
 observation_ref TEXT REFERENCES tool_events(tool_event_id), nonmaterial_note TEXT CHECK(nonmaterial_note IS NULL OR length(nonmaterial_note)<=280),
 status TEXT NOT NULL CHECK(status IN ('PROPOSED','ESCALATION_ACK','REFUSED','UNABLE')),
 response_turn INTEGER NOT NULL CHECK(response_turn>=1),
 CHECK((status='PROPOSED' AND purpose IS NOT NULL AND template_variant IS NOT NULL
        AND selected_field_ids_json IS NOT NULL AND observation_ref IS NOT NULL AND nonmaterial_note IS NULL)
       OR (status IN ('ESCALATION_ACK','REFUSED','UNABLE') AND purpose IS NULL
           AND template_variant IS NULL AND selected_field_ids_json IS NULL AND observation_ref IS NULL)),
 FOREIGN KEY(invocation_id,episode_id) REFERENCES agent_invocations(invocation_id,episode_id),
 FOREIGN KEY(handoff_id,episode_id) REFERENCES handoffs(handoff_id,episode_id),
 FOREIGN KEY(observation_ref,invocation_id) REFERENCES tool_events(tool_event_id,invocation_id));
CREATE TRIGGER agent_b_proposals_scope_insert BEFORE INSERT ON agent_b_proposals
 BEGIN
  SELECT RAISE(ABORT,'B invocation required')
   WHERE (SELECT agent_role FROM agent_invocations WHERE invocation_id=NEW.invocation_id) <> 'B';
  SELECT RAISE(ABORT,'scoped B observation required')
   WHERE NEW.status='PROPOSED' AND NOT EXISTS (
    SELECT 1 FROM tool_events e WHERE e.tool_event_id=NEW.observation_ref
      AND e.invocation_id=NEW.invocation_id AND e.handoff_id=NEW.handoff_id
      AND e.verification_id IS NOT NULL AND e.tool_name='read_verified_order_status'
      AND e.tool_status='SUCCESS'
      AND e.observation_json IS NOT NULL AND e.observation_owner IS NOT NULL AND e.observed_at IS NOT NULL
      AND (SELECT episode_id FROM handoffs WHERE handoff_id=e.handoff_id)=NEW.episode_id
      AND (SELECT episode_id FROM verifications WHERE verification_id=e.verification_id)=NEW.episode_id
      AND e.observation_sent_turn IS NOT NULL AND e.continuation_turn IS NOT NULL
      AND e.observation_sent_turn < e.continuation_turn AND e.continuation_turn <= NEW.response_turn);
 END;
CREATE TRIGGER agent_b_proposals_scope_update BEFORE UPDATE ON agent_b_proposals
 BEGIN
  SELECT RAISE(ABORT,'B invocation required')
   WHERE (SELECT agent_role FROM agent_invocations WHERE invocation_id=NEW.invocation_id) <> 'B';
  SELECT RAISE(ABORT,'scoped B observation required')
   WHERE NEW.status='PROPOSED' AND NOT EXISTS (
    SELECT 1 FROM tool_events e WHERE e.tool_event_id=NEW.observation_ref
      AND e.invocation_id=NEW.invocation_id AND e.handoff_id=NEW.handoff_id
      AND e.verification_id IS NOT NULL AND e.tool_name='read_verified_order_status'
      AND e.tool_status='SUCCESS'
      AND e.observation_json IS NOT NULL AND e.observation_owner IS NOT NULL AND e.observed_at IS NOT NULL
      AND (SELECT episode_id FROM handoffs WHERE handoff_id=e.handoff_id)=NEW.episode_id
      AND (SELECT episode_id FROM verifications WHERE verification_id=e.verification_id)=NEW.episode_id
      AND e.observation_sent_turn IS NOT NULL AND e.continuation_turn IS NOT NULL
      AND e.observation_sent_turn < e.continuation_turn AND e.continuation_turn <= NEW.response_turn);
 END;
CREATE TABLE clarification_drafts (
 draft_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 question_text TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE material_actions (
 material_action_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 action_id TEXT NOT NULL UNIQUE, business_key TEXT NOT NULL CHECK(length(business_key)=64 AND business_key NOT GLOB '*[^0-9a-f]*'),
 canonical_schema_version INTEGER NOT NULL CHECK(canonical_schema_version=1),
 operation_class TEXT NOT NULL CHECK(operation_class IN ('ROUTINE_ORDER_STATUS','SANITIZED_INTERNAL_ESCALATION')),
 org_id TEXT NOT NULL REFERENCES organizations(org_id), grant_id TEXT NOT NULL REFERENCES manager_grants(grant_id),
 ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id), source_version INTEGER NOT NULL CHECK(source_version>=1),
 account_id TEXT REFERENCES accounts(account_id), order_id TEXT REFERENCES orders(order_id),
 recipient_contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
 field_values_json TEXT NOT NULL CHECK(json_valid(field_values_json)), template_id TEXT NOT NULL,
 rendered_body TEXT NOT NULL, binding_versions_json TEXT NOT NULL CHECK(json_valid(binding_versions_json)),
 canonical_json TEXT NOT NULL CHECK(json_valid(canonical_json)),
 material_sha256 TEXT NOT NULL CHECK(length(material_sha256)=64 AND material_sha256 NOT GLOB '*[^0-9a-f]*'),
 created_at TEXT NOT NULL,
 UNIQUE(material_action_id,material_sha256),
 UNIQUE(material_action_id,action_id,business_key,material_sha256,recipient_contact_id,rendered_body));
CREATE TABLE escalation_approvals (
 approval_id TEXT PRIMARY KEY, material_action_id TEXT NOT NULL REFERENCES material_actions(material_action_id),
 material_sha256 TEXT NOT NULL, supervisor_id TEXT REFERENCES principals(principal_id),
 supervisor_version INTEGER, decision TEXT CHECK(decision IS NULL OR decision IN ('APPROVE','REJECT')),
 decided_at TEXT, status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','EXPIRED','REVOKED','SUPERSEDED','CONSUMED')),
 approved_at TEXT, expires_at TEXT NOT NULL, revoked_at TEXT,
 consumed_effect_id TEXT UNIQUE REFERENCES queue_effects(effect_id), version INTEGER NOT NULL CHECK(version>=1),
 FOREIGN KEY(material_action_id,material_sha256) REFERENCES material_actions(material_action_id,material_sha256),
 CHECK((supervisor_id IS NULL AND supervisor_version IS NULL AND decision IS NULL AND decided_at IS NULL)
       OR (supervisor_id IS NOT NULL AND supervisor_version IS NOT NULL AND supervisor_version>=1
           AND decision IS NOT NULL AND decided_at IS NOT NULL)),
 CHECK((status='REVOKED' AND revoked_at IS NOT NULL) OR (status<>'REVOKED' AND revoked_at IS NULL)),
 CHECK((status='PENDING' AND decision IS NULL AND approved_at IS NULL AND consumed_effect_id IS NULL)
    OR (status='APPROVED' AND decision='APPROVE' AND approved_at IS NOT NULL AND consumed_effect_id IS NULL)
    OR (status='REJECTED' AND decision='REJECT' AND approved_at IS NULL AND consumed_effect_id IS NULL)
    OR (status='CONSUMED' AND decision='APPROVE' AND approved_at IS NOT NULL AND consumed_effect_id IS NOT NULL)
    OR (status IN ('EXPIRED','REVOKED','SUPERSEDED') AND consumed_effect_id IS NULL
        AND ((decision='APPROVE' AND approved_at IS NOT NULL)
          OR (decision='REJECT' AND approved_at IS NULL)
          OR (decision IS NULL AND approved_at IS NULL)))));
CREATE UNIQUE INDEX one_active_approval_per_material ON escalation_approvals(material_action_id)
 WHERE status IN ('PENDING','APPROVED');
CREATE TABLE resource_attempts (
 attempt_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, business_key TEXT NOT NULL,
 material_sha256 TEXT NOT NULL, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 submitted_at TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('NOT_ATTEMPTED','ACCEPTED_COMMITTED','EXPLICIT_POLICY_DENIAL','TECHNICAL_ERROR','INDETERMINATE','RECONCILED_COMMITTED','RECONCILED_NOT_COMMITTED')),
 reason_code TEXT, effect_id TEXT REFERENCES queue_effects(effect_id), resolution_at TEXT);
CREATE TABLE queue_effects (
 effect_id TEXT PRIMARY KEY, action_id TEXT NOT NULL UNIQUE, business_key TEXT NOT NULL UNIQUE,
 material_action_id TEXT NOT NULL REFERENCES material_actions(material_action_id),
 recipient_contact_id TEXT NOT NULL REFERENCES contacts(contact_id), material_sha256 TEXT NOT NULL,
 body_text TEXT NOT NULL, committed_at TEXT NOT NULL,
 FOREIGN KEY(material_action_id,action_id,business_key,material_sha256,recipient_contact_id,body_text)
 REFERENCES material_actions(material_action_id,action_id,business_key,material_sha256,recipient_contact_id,rendered_body));
CREATE TABLE journal_events (
 event_seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES resource_attempts(attempt_id),
 action_id TEXT NOT NULL, event_type TEXT NOT NULL, reason_code TEXT,
 effect_id TEXT REFERENCES queue_effects(effect_id), occurred_at TEXT NOT NULL,
 details_json TEXT NOT NULL CHECK(json_valid(details_json)));
CREATE TABLE evaluation_truth (
 truth_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
 truth_version TEXT NOT NULL, expected_intent_json TEXT NOT NULL CHECK(json_valid(expected_intent_json)),
 allowed_effects_json TEXT NOT NULL CHECK(json_valid(allowed_effects_json)), annotation_notes TEXT);
CREATE TABLE evaluation_results (
 result_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
 scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
 baseline TEXT NOT NULL CHECK(baseline IN ('B0','B1','S5')),
 model_config_json TEXT NOT NULL CHECK(json_valid(model_config_json)),
 observed_result_json TEXT NOT NULL CHECK(json_valid(observed_result_json)),
 evidence_complete INTEGER NOT NULL CHECK(evidence_complete IN (0,1)), evaluated_at TEXT NOT NULL);
CREATE INDEX idx_grants_ticket ON manager_grants(scope_ticket_id);
CREATE INDEX idx_tickets_grant ON tickets(assigned_grant_id);
CREATE INDEX idx_accounts_customer ON accounts(customer_id);
CREATE INDEX idx_orders_account ON orders(account_id);
CREATE INDEX idx_contacts_customer_state ON contacts(customer_id,active,verified);
CREATE INDEX idx_approvals_material_status ON escalation_approvals(material_action_id,status);
CREATE INDEX idx_journal_action_seq ON journal_events(action_id,event_seq);
CREATE INDEX idx_invocations_episode ON agent_invocations(episode_id);
CREATE INDEX idx_tools_invocation ON tool_events(invocation_id);
