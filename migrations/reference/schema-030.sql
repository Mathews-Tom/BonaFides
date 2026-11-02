-- SPDX-License-Identifier: MIT OR Apache-2.0
-- Normalised schema of a fresh database built by the 30-migration chain that the
-- 0.1.0 baseline replaced; written by `python -m tools.schema_equivalence reference`.

-- Name: COLUMN action_approvals.approved_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(approved_at) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN action_approvals.approved_auth_epoch; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(approved_auth_epoch) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN action_approvals.approved_session_id; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(approved_session_id) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN action_approvals.consumed_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(consumed_at) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN action_approvals.revoked_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(revoked_at) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN action_approvals.version; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(version) ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: COLUMN hosted_egress_consents.revoked_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(revoked_at) ON TABLE bonafides.hosted_egress_consents TO bonafides_runtime;

-- Name: COLUMN hosted_egress_consents.transmission_count; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(transmission_count) ON TABLE bonafides.hosted_egress_consents TO bonafides_runtime;

-- Name: COLUMN installation_backups.cipher_verified_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(cipher_verified_at) ON TABLE bonafides.installation_backups TO bonafides_runtime;

-- Name: COLUMN installation_backups.copy_pruned_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(copy_pruned_at) ON TABLE bonafides.installation_backups TO bonafides_runtime;

-- Name: COLUMN installation_backups.copy_verified_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(copy_verified_at) ON TABLE bonafides.installation_backups TO bonafides_runtime;

-- Name: COLUMN installation_backups.pruned_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(pruned_at) ON TABLE bonafides.installation_backups TO bonafides_runtime;

-- Name: COLUMN installation_state.bootstrap_consumed_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(bootstrap_consumed_at) ON TABLE bonafides.installation_state TO bonafides_runtime;

-- Name: COLUMN installation_state.bootstrap_expires_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(bootstrap_expires_at) ON TABLE bonafides.installation_state TO bonafides_runtime;

-- Name: COLUMN installation_state.bootstrap_token_sha256; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(bootstrap_token_sha256) ON TABLE bonafides.installation_state TO bonafides_runtime;

-- Name: COLUMN local_model_route_blocks.route_sha256; Type: COMMENT; Schema: bonafides; Owner: bonafides_migrator

COMMENT ON COLUMN bonafides.local_model_route_blocks.route_sha256 IS 'Capability SHA-256 for new blocks; legacy hashes apply only while route identity is unchanged.';

-- Name: COLUMN portable_exports.state; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(state) ON TABLE bonafides.portable_exports TO bonafides_runtime;

-- Name: COLUMN portable_previews.candidate_bytes; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(candidate_bytes) ON TABLE bonafides.portable_previews TO bonafides_runtime;

-- Name: COLUMN portable_previews.clear_reason; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(clear_reason) ON TABLE bonafides.portable_previews TO bonafides_runtime;

-- Name: COLUMN portable_previews.cleared_at; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT UPDATE(cleared_at) ON TABLE bonafides.portable_previews TO bonafides_runtime;

-- Name: FUNCTION abort_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.abort_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;

-- Name: FUNCTION assert_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.assert_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.assert_installation_control(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION assert_portable_checkpoint(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.assert_portable_checkpoint(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.assert_portable_checkpoint(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb) TO bonafides_runtime;

-- Name: FUNCTION assert_workflow_fence(p_owner uuid, p_run uuid, p_worker uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.assert_workflow_fence(p_owner uuid, p_run uuid, p_worker uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.assert_workflow_fence(p_owner uuid, p_run uuid, p_worker uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION begin_guarded_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid, p_guard uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.begin_guarded_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid, p_guard uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.begin_guarded_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid, p_guard uuid) TO bonafides_runtime;

-- Name: FUNCTION begin_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.begin_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid) FROM PUBLIC;

-- Name: FUNCTION begin_maintenance_recovery(p_recovery uuid, p_namespace uuid, p_epoch bigint, p_control uuid, p_previous_holder uuid, p_previous_fence bigint, p_guard uuid, p_parser text, p_renderer text, p_holder uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.begin_maintenance_recovery(p_recovery uuid, p_namespace uuid, p_epoch bigint, p_control uuid, p_previous_holder uuid, p_previous_fence bigint, p_guard uuid, p_parser text, p_renderer text, p_holder uuid) FROM PUBLIC;

-- Name: FUNCTION bind_execution_guard(p_guard uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.bind_execution_guard(p_guard uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.bind_execution_guard(p_guard uuid) TO bonafides_runtime;

-- Name: FUNCTION check_observation_source(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.check_observation_source() FROM PUBLIC;

-- Name: FUNCTION check_source_review(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.check_source_review() FROM PUBLIC;

-- Name: FUNCTION clear_erased_portable_previews(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.clear_erased_portable_previews() FROM PUBLIC;

-- Name: FUNCTION clear_portable_preview_chunks(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.clear_portable_preview_chunks() FROM PUBLIC;

-- Name: FUNCTION complete_portable_purge(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.complete_portable_purge(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.complete_portable_purge(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION erase_portable_scope(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb, p_orphans jsonb); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.erase_portable_scope(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb, p_orphans jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.erase_portable_scope(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb, p_orphans jsonb) TO bonafides_runtime;

-- Name: FUNCTION fail_portable_export_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.fail_portable_export_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.fail_portable_export_control(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION finalize_portable_preview(p_owner uuid, p_id uuid, p_plan jsonb, p_sha text, p_expiry timestamp with time zone); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.finalize_portable_preview(p_owner uuid, p_id uuid, p_plan jsonb, p_sha text, p_expiry timestamp with time zone) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.finalize_portable_preview(p_owner uuid, p_id uuid, p_plan jsonb, p_sha text, p_expiry timestamp with time zone) TO bonafides_runtime;

-- Name: FUNCTION guard_import_batch(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.guard_import_batch() FROM PUBLIC;

-- Name: FUNCTION guard_portable_preview_chunk(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.guard_portable_preview_chunk() FROM PUBLIC;

-- Name: FUNCTION guard_posting_source(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.guard_posting_source() FROM PUBLIC;

-- Name: FUNCTION guard_source_identity(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.guard_source_identity() FROM PUBLIC;

-- Name: FUNCTION guard_source_workflow_pin(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.guard_source_workflow_pin() FROM PUBLIC;

-- Name: FUNCTION heartbeat_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.heartbeat_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.heartbeat_installation_control(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION inspect_maintenance_recovery(p_namespace uuid, p_epoch bigint, p_control uuid, p_holder uuid, p_fence bigint, p_guard uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.inspect_maintenance_recovery(p_namespace uuid, p_epoch bigint, p_control uuid, p_holder uuid, p_fence bigint, p_guard uuid) FROM PUBLIC;

-- Name: FUNCTION invalidate_posting_requirement_projection(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.invalidate_posting_requirement_projection() FROM PUBLIC;

-- Name: FUNCTION protect_action_approval_binding(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.protect_action_approval_binding() FROM PUBLIC;

-- Name: FUNCTION protect_hosted_consent_binding(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.protect_hosted_consent_binding() FROM PUBLIC;

-- Name: FUNCTION quiesce_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.quiesce_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.quiesce_installation_control(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION register_installation_backup(p_id uuid, p_holder uuid, p_fence bigint, p_record jsonb); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.register_installation_backup(p_id uuid, p_holder uuid, p_fence bigint, p_record jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.register_installation_backup(p_id uuid, p_holder uuid, p_fence bigint, p_record jsonb) TO bonafides_runtime;

-- Name: FUNCTION register_portable_staging(p_id uuid, p_holder uuid, p_fence bigint, p_sha text, p_length bigint, p_staging text); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.register_portable_staging(p_id uuid, p_holder uuid, p_fence bigint, p_sha text, p_length bigint, p_staging text) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.register_portable_staging(p_id uuid, p_holder uuid, p_fence bigint, p_sha text, p_length bigint, p_staging text) TO bonafides_runtime;

-- Name: FUNCTION reject_revision_update(); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.reject_revision_update() FROM PUBLIC;

-- Name: FUNCTION release_aborted_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.release_aborted_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;

-- Name: FUNCTION release_installation_control(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.release_installation_control(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.release_installation_control(p_id uuid, p_holder uuid, p_fence bigint) TO bonafides_runtime;

-- Name: FUNCTION release_installation_control_engine(p_id uuid, p_holder uuid, p_fence bigint); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.release_installation_control_engine(p_id uuid, p_holder uuid, p_fence bigint) FROM PUBLIC;

-- Name: FUNCTION release_orphan_recovery(p_id uuid, p_namespace uuid, p_epoch bigint, p_guard uuid); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.release_orphan_recovery(p_id uuid, p_namespace uuid, p_epoch bigint, p_guard uuid) FROM PUBLIC;

-- Name: FUNCTION resolve_portable_purge_item(p_id uuid, p_holder uuid, p_fence bigint, p_kind text, p_key text, p_outcome text); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.resolve_portable_purge_item(p_id uuid, p_holder uuid, p_fence bigint, p_kind text, p_key text, p_outcome text) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.resolve_portable_purge_item(p_id uuid, p_holder uuid, p_fence bigint, p_kind text, p_key text, p_outcome text) TO bonafides_runtime;

-- Name: FUNCTION settle_installation_control(p_id uuid, p_holder uuid, p_fence bigint, p_result jsonb); Type: ACL; Schema: bonafides; Owner: bonafides_migrator

REVOKE ALL ON FUNCTION bonafides.settle_installation_control(p_id uuid, p_holder uuid, p_fence bigint, p_result jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION bonafides.settle_installation_control(p_id uuid, p_holder uuid, p_fence bigint, p_result jsonb) TO bonafides_runtime;

-- Name: SCHEMA bonafides; Type: ACL; Schema: -; Owner: bonafides_migrator

GRANT USAGE ON SCHEMA bonafides TO bonafides_runtime;

-- Name: TABLE action_approvals; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.action_approvals TO bonafides_runtime;

-- Name: TABLE application_attachments; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.application_attachments TO bonafides_runtime;

-- Name: TABLE application_contacts; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.application_contacts TO bonafides_runtime;

-- Name: TABLE application_notes; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.application_notes TO bonafides_runtime;

-- Name: TABLE application_stage_events; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.application_stage_events TO bonafides_runtime;

-- Name: TABLE applications; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.applications TO bonafides_runtime;

-- Name: TABLE appointments; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.appointments TO bonafides_runtime;

-- Name: TABLE approval_audit_events; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.approval_audit_events TO bonafides_runtime;

-- Name: TABLE audit_events; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.audit_events TO bonafides_runtime;

-- Name: TABLE auth_challenges; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE bonafides.auth_challenges TO bonafides_runtime;

-- Name: TABLE auth_throttles; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE bonafides.auth_throttles TO bonafides_runtime;

-- Name: TABLE backup_daily_runs; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.backup_daily_runs TO bonafides_runtime;

-- Name: TABLE blobs; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.blobs TO bonafides_runtime;

-- Name: TABLE claim_evidence_links; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.claim_evidence_links TO bonafides_runtime;

-- Name: TABLE claim_revisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.claim_revisions TO bonafides_runtime;

-- Name: TABLE credential_key_usage; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.credential_key_usage TO bonafides_runtime;

-- Name: TABLE document_artifacts; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.document_artifacts TO bonafides_runtime;

-- Name: TABLE document_claim_links; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.document_claim_links TO bonafides_runtime;

-- Name: TABLE document_drafts; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.document_drafts TO bonafides_runtime;

-- Name: TABLE document_regeneration_proposals; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.document_regeneration_proposals TO bonafides_runtime;

-- Name: TABLE document_reviews; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.document_reviews TO bonafides_runtime;

-- Name: TABLE document_revisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.document_revisions TO bonafides_runtime;

-- Name: TABLE encrypted_credentials; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.encrypted_credentials TO bonafides_runtime;

-- Name: TABLE erased_request_keys; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.erased_request_keys TO bonafides_runtime;

-- Name: TABLE event_metadata; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.event_metadata TO bonafides_runtime;

-- Name: TABLE event_receipts; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.event_receipts TO bonafides_runtime;

-- Name: TABLE evidence_classification_changes; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.evidence_classification_changes TO bonafides_runtime;

-- Name: TABLE evidence_records; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.evidence_records TO bonafides_runtime;

-- Name: TABLE evidence_revisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.evidence_revisions TO bonafides_runtime;

-- Name: TABLE external_accounts; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.external_accounts TO bonafides_runtime;

-- Name: TABLE hosted_egress_consents; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.hosted_egress_consents TO bonafides_runtime;

-- Name: TABLE idempotency_records; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.idempotency_records TO bonafides_runtime;

-- Name: TABLE import_batches; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.import_batches TO bonafides_runtime;

-- Name: TABLE installation_activities; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE ON TABLE bonafides.installation_activities TO bonafides_runtime;

-- Name: TABLE installation_backups; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.installation_backups TO bonafides_runtime;

-- Name: TABLE installation_controls; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.installation_controls TO bonafides_runtime;

-- Name: TABLE installation_state; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.installation_state TO bonafides_runtime;

-- Name: TABLE job_postings; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.job_postings TO bonafides_runtime;

-- Name: TABLE job_source_observations; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.job_source_observations TO bonafides_runtime;

-- Name: TABLE local_model_reservations; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,UPDATE ON TABLE bonafides.local_model_reservations TO bonafides_runtime;

-- Name: TABLE local_model_route_blocks; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.local_model_route_blocks TO bonafides_runtime;

-- Name: TABLE maintenance_recoveries; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.maintenance_recoveries TO bonafides_runtime;

-- Name: TABLE match_assessments; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.match_assessments TO bonafides_runtime;

-- Name: TABLE model_runs; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.model_runs TO bonafides_runtime;

-- Name: TABLE oauth_transactions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE bonafides.oauth_transactions TO bonafides_runtime;

-- Name: TABLE outbox_events; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.outbox_events TO bonafides_runtime;

-- Name: TABLE owner_settings; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.owner_settings TO bonafides_runtime;

-- Name: TABLE owners; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.owners TO bonafides_runtime;

-- Name: TABLE portable_exports; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.portable_exports TO bonafides_runtime;

-- Name: TABLE portable_model_history; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.portable_model_history TO bonafides_runtime;

-- Name: TABLE portable_preview_chunks; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.portable_preview_chunks TO bonafides_runtime;

-- Name: TABLE portable_previews; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.portable_previews TO bonafides_runtime;

-- Name: TABLE portable_purge_items; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.portable_purge_items TO bonafides_runtime;

-- Name: TABLE portable_purges; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.portable_purges TO bonafides_runtime;

-- Name: TABLE portable_staged_files; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.portable_staged_files TO bonafides_runtime;

-- Name: TABLE posting_duplicate_decisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.posting_duplicate_decisions TO bonafides_runtime;

-- Name: TABLE posting_requirement_revisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.posting_requirement_revisions TO bonafides_runtime;

-- Name: TABLE professional_claims; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.professional_claims TO bonafides_runtime;

-- Name: TABLE profile_revision_claims; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.profile_revision_claims TO bonafides_runtime;

-- Name: TABLE profile_revision_evidence; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.profile_revision_evidence TO bonafides_runtime;

-- Name: TABLE profile_revisions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.profile_revisions TO bonafides_runtime;

-- Name: TABLE profiles; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.profiles TO bonafides_runtime;

-- Name: TABLE provider_executions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE ON TABLE bonafides.provider_executions TO bonafides_runtime;

-- Name: TABLE saved_opportunities; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.saved_opportunities TO bonafides_runtime;

-- Name: TABLE schema_migrations; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT ON TABLE bonafides.schema_migrations TO bonafides_runtime;

-- Name: TABLE sessions; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.sessions TO bonafides_runtime;

-- Name: TABLE source_configs; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.source_configs TO bonafides_runtime;

-- Name: TABLE source_policy_reviews; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.source_policy_reviews TO bonafides_runtime;

-- Name: TABLE transfer_manifests; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.transfer_manifests TO bonafides_runtime;

-- Name: TABLE worker_heartbeats; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE bonafides.worker_heartbeats TO bonafides_runtime;

-- Name: TABLE workflow_attempt_events; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT ON TABLE bonafides.workflow_attempt_events TO bonafides_runtime;

-- Name: TABLE workflow_runs; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.workflow_runs TO bonafides_runtime;

-- Name: TABLE workflow_steps; Type: ACL; Schema: bonafides; Owner: bonafides_migrator

GRANT SELECT,INSERT,UPDATE ON TABLE bonafides.workflow_steps TO bonafides_runtime;

-- Name: abort_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.abort_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR EXISTS(SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id)
 OR NOT EXISTS(SELECT 1 FROM bonafides.maintenance_recoveries WHERE control_id=c.id
  AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL AND deadline_at>clock_timestamp()) THEN
  RAISE EXCEPTION 'precommit abort refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.portable_previews SET candidate_bytes=NULL,cleared_at=clock_timestamp(),clear_reason='cancelled'
 WHERE workflow_run_id=c.id AND cleared_at IS NULL;
 UPDATE bonafides.portable_exports SET state='expired' WHERE workflow_run_id=c.id;
 UPDATE bonafides.installation_controls SET phase='settled',settled_at=clock_timestamp(),
  result='{"outcome":"ABORTED","error_code":"maintenance_aborted"}' WHERE id=c.id;
END $$;


ALTER FUNCTION bonafides.abort_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: action_approvals action_approvals_owner_id_document_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.action_approvals
    ADD CONSTRAINT action_approvals_owner_id_document_revision_id_fkey FOREIGN KEY (owner_id, document_revision_id) REFERENCES bonafides.document_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: action_approvals action_approvals_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.action_approvals
    ADD CONSTRAINT action_approvals_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: action_approvals action_approvals_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.action_approvals
    ADD CONSTRAINT action_approvals_pkey PRIMARY KEY (owner_id, id);

-- Name: action_approvals action_approvals_workflow_identity_unique; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.action_approvals
    ADD CONSTRAINT action_approvals_workflow_identity_unique UNIQUE (owner_id, id, workflow_run_id);

-- Name: action_approvals immutable_action_binding; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_action_binding BEFORE UPDATE ON bonafides.action_approvals FOR EACH ROW EXECUTE FUNCTION bonafides.protect_action_approval_binding();

-- Name: action_approvals; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.action_approvals (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    action_kind text NOT NULL,
    payload_sha256 text NOT NULL,
    recipient_sha256 text,
    document_revision_id uuid,
    expires_at timestamp with time zone NOT NULL,
    approved_at timestamp with time zone,
    consumed_at timestamp with time zone,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    intent jsonb,
    destination_sha256 text,
    target_version bigint,
    approved_session_id uuid,
    approved_auth_epoch bigint,
    revoked_at timestamp with time zone,
    CONSTRAINT action_approvals_action_kind_check CHECK ((action_kind = ANY (ARRAY['send_mail'::text, 'calendar_write'::text, 'hosted_egress'::text, 'replace_workspace'::text, 'privacy_delete'::text]))),
    CONSTRAINT action_approvals_approval_session_check CHECK (((approved_session_id IS NULL) = (approved_auth_epoch IS NULL))),
    CONSTRAINT action_approvals_approved_auth_epoch_check CHECK ((approved_auth_epoch > 0)),
    CONSTRAINT action_approvals_check CHECK ((expires_at > created_at)),
    CONSTRAINT action_approvals_check1 CHECK (((consumed_at IS NULL) OR (approved_at IS NOT NULL))),
    CONSTRAINT action_approvals_check2 CHECK (((action_kind <> 'send_mail'::text) OR ((recipient_sha256 IS NOT NULL) AND (document_revision_id IS NOT NULL)))),
    CONSTRAINT action_approvals_destination_sha256_check CHECK (((destination_sha256 IS NULL) OR (destination_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT action_approvals_exact_binding_check CHECK (((intent IS NULL) OR ((destination_sha256 IS NOT NULL) AND (target_version IS NOT NULL)))),
    CONSTRAINT action_approvals_exact_session_check CHECK (((intent IS NULL) OR (approved_at IS NULL) OR (approved_session_id IS NOT NULL))),
    CONSTRAINT action_approvals_intent_check CHECK (((intent IS NULL) OR (jsonb_typeof(intent) = 'object'::text))),
    CONSTRAINT action_approvals_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT action_approvals_recipient_sha256_check CHECK ((recipient_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT action_approvals_target_version_check CHECK ((target_version > 0)),
    CONSTRAINT action_approvals_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.action_approvals OWNER TO bonafides_migrator;

-- Name: action_approvals_exact_workflow_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX action_approvals_exact_workflow_idx ON bonafides.action_approvals USING btree (owner_id, workflow_run_id) WHERE (intent IS NOT NULL);

-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: bonafides_migrations; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides_migrations.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);

-- Name: alembic_version; Type: TABLE; Schema: bonafides_migrations; Owner: bonafides_migrator

CREATE TABLE bonafides_migrations.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE bonafides_migrations.alembic_version OWNER TO bonafides_migrator;

-- Name: application_attachments application_attachments_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_attachments
    ADD CONSTRAINT application_attachments_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: application_attachments application_attachments_owner_id_blob_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_attachments
    ADD CONSTRAINT application_attachments_owner_id_blob_id_fkey FOREIGN KEY (owner_id, blob_id) REFERENCES bonafides.blobs(owner_id, id) ON DELETE RESTRICT;

-- Name: application_attachments application_attachments_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_attachments
    ADD CONSTRAINT application_attachments_pkey PRIMARY KEY (owner_id, id);

-- Name: application_attachments; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.application_attachments (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    blob_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT application_attachments_display_name_check CHECK ((length(display_name) <= 255))
);


ALTER TABLE bonafides.application_attachments OWNER TO bonafides_migrator;

-- Name: application_contacts application_contacts_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_contacts
    ADD CONSTRAINT application_contacts_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: application_contacts application_contacts_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_contacts
    ADD CONSTRAINT application_contacts_pkey PRIMARY KEY (owner_id, id);

-- Name: application_contacts; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.application_contacts (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    display_name text NOT NULL,
    email text,
    role_label text,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT application_contacts_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.application_contacts OWNER TO bonafides_migrator;

-- Name: application_notes application_notes_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_notes
    ADD CONSTRAINT application_notes_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: application_notes application_notes_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_notes
    ADD CONSTRAINT application_notes_pkey PRIMARY KEY (owner_id, id);

-- Name: application_notes; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.application_notes (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    body text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT application_notes_body_check CHECK ((length(body) <= 50000)),
    CONSTRAINT application_notes_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.application_notes OWNER TO bonafides_migrator;

-- Name: application_stage; Type: TYPE; Schema: bonafides; Owner: bonafides_migrator

CREATE TYPE bonafides.application_stage AS ENUM (
    'preparing',
    'applied',
    'screening',
    'interview',
    'offer',
    'accepted',
    'rejected',
    'withdrawn'
);


ALTER TYPE bonafides.application_stage OWNER TO bonafides_migrator;

-- Name: application_stage_events application_stage_events_owner_id_application_id_correctio_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_owner_id_application_id_correctio_fkey FOREIGN KEY (owner_id, application_id, correction_of) REFERENCES bonafides.application_stage_events(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: application_stage_events application_stage_events_owner_id_application_id_correction_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_owner_id_application_id_correction_key UNIQUE (owner_id, application_id, correction_of);

-- Name: application_stage_events application_stage_events_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: application_stage_events application_stage_events_owner_id_application_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_owner_id_application_id_id_key UNIQUE (owner_id, application_id, id);

-- Name: application_stage_events application_stage_events_owner_id_application_id_sequence_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_owner_id_application_id_sequence_key UNIQUE (owner_id, application_id, sequence);

-- Name: application_stage_events application_stage_events_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.application_stage_events
    ADD CONSTRAINT application_stage_events_pkey PRIMARY KEY (owner_id, id);

-- Name: application_stage_events immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.application_stage_events FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: application_stage_events; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.application_stage_events (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    from_stage bonafides.application_stage,
    to_stage bonafides.application_stage NOT NULL,
    effective_at timestamp with time zone NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sequence bigint NOT NULL,
    correction_of uuid,
    affects_current_stage boolean DEFAULT true NOT NULL,
    CONSTRAINT application_stage_events_check CHECK (((correction_of IS NULL) OR (correction_of <> id))),
    CONSTRAINT application_stage_events_sequence_check CHECK ((sequence > 0))
);


ALTER TABLE bonafides.application_stage_events OWNER TO bonafides_migrator;

-- Name: applications applications_owner_id_id_current_stage_event_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.applications
    ADD CONSTRAINT applications_owner_id_id_current_stage_event_id_fkey FOREIGN KEY (owner_id, id, current_stage_event_id) REFERENCES bonafides.application_stage_events(owner_id, application_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: applications applications_owner_id_opportunity_id_attempt_no_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.applications
    ADD CONSTRAINT applications_owner_id_opportunity_id_attempt_no_key UNIQUE (owner_id, opportunity_id, attempt_no);

-- Name: applications applications_owner_id_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.applications
    ADD CONSTRAINT applications_owner_id_opportunity_id_fkey FOREIGN KEY (owner_id, opportunity_id) REFERENCES bonafides.saved_opportunities(owner_id, id) ON DELETE RESTRICT;

-- Name: applications applications_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.applications
    ADD CONSTRAINT applications_pkey PRIMARY KEY (owner_id, id);

-- Name: applications; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.applications (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    opportunity_id uuid NOT NULL,
    current_stage bonafides.application_stage DEFAULT 'preparing'::bonafides.application_stage NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    attempt_no bigint DEFAULT 1 NOT NULL,
    current_stage_event_id uuid,
    CONSTRAINT applications_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT applications_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.applications OWNER TO bonafides_migrator;

-- Name: applications_active_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX applications_active_idx ON bonafides.applications USING btree (owner_id, created_at, id) WHERE (archived_at IS NULL);

-- Name: applications_list_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX applications_list_idx ON bonafides.applications USING btree (owner_id, created_at, id);

-- Name: applications_opportunity_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX applications_opportunity_idx ON bonafides.applications USING btree (owner_id, opportunity_id);

-- Name: appointments appointments_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.appointments
    ADD CONSTRAINT appointments_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: appointments appointments_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.appointments
    ADD CONSTRAINT appointments_pkey PRIMARY KEY (owner_id, id);

-- Name: appointments; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.appointments (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    timezone text NOT NULL,
    local_start timestamp without time zone NOT NULL,
    duration_minutes integer,
    completed_at timestamp with time zone,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    ends_at timestamp with time zone,
    local_end timestamp without time zone,
    display_timezone text NOT NULL,
    status text DEFAULT 'scheduled'::text NOT NULL,
    cancelled_at timestamp with time zone,
    CONSTRAINT appointments_check CHECK (((status = 'completed'::text) = (completed_at IS NOT NULL))),
    CONSTRAINT appointments_check1 CHECK (((status = 'cancelled'::text) = (cancelled_at IS NOT NULL))),
    CONSTRAINT appointments_check2 CHECK (((ends_at IS NULL) OR (ends_at > starts_at))),
    CONSTRAINT appointments_duration_minutes_check CHECK (((duration_minutes >= 1) AND (duration_minutes <= 1440))),
    CONSTRAINT appointments_kind_check CHECK ((kind = ANY (ARRAY['interview'::text, 'follow_up'::text]))),
    CONSTRAINT appointments_status_check CHECK ((status = ANY (ARRAY['scheduled'::text, 'completed'::text, 'cancelled'::text]))),
    CONSTRAINT appointments_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.appointments OWNER TO bonafides_migrator;

-- Name: appointments_due_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX appointments_due_idx ON bonafides.appointments USING btree (owner_id, starts_at) WHERE (completed_at IS NULL);

-- Name: approval_audit_events approval_audit_events_owner_id_approval_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.approval_audit_events
    ADD CONSTRAINT approval_audit_events_owner_id_approval_id_workflow_run_id_fkey FOREIGN KEY (owner_id, approval_id, workflow_run_id) REFERENCES bonafides.action_approvals(owner_id, id, workflow_run_id) ON DELETE CASCADE;

-- Name: approval_audit_events approval_audit_events_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.approval_audit_events
    ADD CONSTRAINT approval_audit_events_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: approval_audit_events approval_audit_events_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.approval_audit_events
    ADD CONSTRAINT approval_audit_events_pkey PRIMARY KEY (owner_id, id);

-- Name: approval_audit_events immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.approval_audit_events FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: approval_audit_events; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.approval_audit_events (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    approval_id uuid NOT NULL,
    workflow_run_id uuid NOT NULL,
    event text NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT approval_audit_events_event_check CHECK ((event = ANY (ARRAY['prepared'::text, 'approved'::text, 'denied'::text, 'consumed'::text, 'revoked'::text, 'expired'::text])))
);


ALTER TABLE bonafides.approval_audit_events OWNER TO bonafides_migrator;

-- Name: approval_audit_history_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX approval_audit_history_idx ON bonafides.approval_audit_events USING btree (owner_id, workflow_run_id, created_at DESC, id DESC);

-- Name: approvals_workflow_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX approvals_workflow_idx ON bonafides.action_approvals USING btree (owner_id, workflow_run_id);

-- Name: assert_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.assert_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS SETOF bonafides.installation_controls
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE
 i bonafides.installation_state%ROWTYPE;
 c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM bonafides.installation_state WHERE singleton FOR SHARE;
 SELECT * INTO STRICT c FROM bonafides.installation_controls WHERE id=p_id FOR UPDATE;
 IF NOT i.maintenance_active OR i.namespace_id<>c.namespace_id
 OR i.maintenance_epoch<>c.maintenance_epoch OR c.holder<>p_holder OR c.fence<>p_fence
 OR c.phase='released' OR c.deadline_at<=clock_timestamp()
 OR c.lease_expires_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM bonafides.workflow_runs w WHERE w.owner_id=c.owner_id
  AND w.id=c.id AND w.state='running' AND w.fence=c.workflow_fence
  AND w.lease_holder=c.holder AND w.cancel_requested_at IS NULL) THEN
  RAISE EXCEPTION 'stale installation control' USING ERRCODE='55000';
 END IF;
 RETURN NEXT c;
END $$;


ALTER FUNCTION bonafides.assert_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: assert_portable_checkpoint(uuid, uuid, bigint, uuid, jsonb); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.assert_portable_checkpoint(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE
 c bonafides.installation_controls%ROWTYPE;
 b bonafides.installation_backups%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 SELECT * INTO STRICT b FROM bonafides.installation_backups WHERE id=p_checkpoint;
 IF c.phase<>'quiescent' OR c.purpose<>'portable_import'
  OR b.owner_id<>c.owner_id OR b.namespace_id<>c.namespace_id
  OR b.maintenance_epoch<>c.maintenance_epoch-1 OR b.restore_verified_at IS NULL
  OR b.workspace_version<>(SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=c.owner_id)
  OR b.owner_auth_epoch<>(SELECT auth_epoch FROM bonafides.owners WHERE id=c.owner_id)
  OR b.installation_auth_epoch<>(SELECT auth_epoch FROM bonafides.installation_state WHERE singleton)
  OR b.schema_versions IS DISTINCT FROM (SELECT jsonb_object_agg(version::text,source_sha256) FROM bonafides.schema_migrations)
  OR p_runtime_pins IS DISTINCT FROM jsonb_build_object(
   'image_sha256',b.image_sha256,'source_sha256',b.source_sha256,
   'config_sha256',b.config_sha256,'keyring_sha256',b.keyring_sha256,
   'recipient_sha256',b.recipient_sha256,'cipher_sha256',b.cipher_sha256)
 THEN
  RAISE EXCEPTION 'fresh verified checkpoint required' USING ERRCODE='55000';
 END IF;
END $$;


ALTER FUNCTION bonafides.assert_portable_checkpoint(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb) OWNER TO bonafides_migrator;

-- Name: assert_workflow_fence(uuid, uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.assert_workflow_fence(p_owner uuid, p_run uuid, p_worker uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
DECLARE w workflow_runs;
BEGIN
 -- Acquire maintenance fence BEFORE workflow lock, consistently with API writers.
 PERFORM 1 FROM installation_state WHERE singleton AND NOT maintenance_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'installation maintenance active' USING ERRCODE='55000'; END IF;
 SELECT * INTO w FROM workflow_runs WHERE owner_id=p_owner AND id=p_run FOR UPDATE;
 IF NOT FOUND OR w.state<>'running' OR w.lease_holder IS DISTINCT FROM p_worker OR w.fence<>p_fence
 OR w.lease_expires_at<=clock_timestamp() OR w.cancel_requested_at IS NOT NULL OR w.deadline_at<=clock_timestamp()
 THEN RAISE EXCEPTION 'workflow lease fenced, cancelled, or expired' USING ERRCODE='55000'; END IF;
 IF w.maintenance_epoch<>(SELECT maintenance_epoch FROM installation_state WHERE singleton)
 THEN RAISE EXCEPTION 'installation epoch changed' USING ERRCODE='55000'; END IF;
END $$;


ALTER FUNCTION bonafides.assert_workflow_fence(p_owner uuid, p_run uuid, p_worker uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: assessments_posting_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX assessments_posting_idx ON bonafides.match_assessments USING btree (owner_id, posting_id, created_at, id);

-- Name: assessments_profile_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX assessments_profile_idx ON bonafides.match_assessments USING btree (owner_id, profile_revision_id);

-- Name: attachments_application_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX attachments_application_idx ON bonafides.application_attachments USING btree (owner_id, application_id, created_at, id);

-- Name: attachments_blob_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX attachments_blob_idx ON bonafides.application_attachments USING btree (owner_id, blob_id);

-- Name: audit_events audit_events_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.audit_events
    ADD CONSTRAINT audit_events_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: audit_events audit_events_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (owner_id, id);

-- Name: audit_events immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.audit_events FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: audit_events; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.audit_events (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action text NOT NULL,
    actor_kind text NOT NULL,
    actor_id text NOT NULL,
    request_id uuid NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT audit_events_actor_kind_check CHECK ((actor_kind = ANY (ARRAY['owner'::text, 'worker'::text, 'operator'::text]))),
    CONSTRAINT audit_events_detail_check CHECK ((jsonb_typeof(detail) = 'object'::text))
);


ALTER TABLE bonafides.audit_events OWNER TO bonafides_migrator;

-- Name: audit_list_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX audit_list_idx ON bonafides.audit_events USING btree (owner_id, created_at, id);

-- Name: auth_challenges auth_challenges_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.auth_challenges
    ADD CONSTRAINT auth_challenges_pkey PRIMARY KEY (token_sha256);

-- Name: auth_challenges; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.auth_challenges (
    token_sha256 text NOT NULL,
    csrf_sha256 text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT auth_challenges_csrf_sha256_check CHECK ((csrf_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT auth_challenges_token_sha256_check CHECK ((token_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.auth_challenges OWNER TO bonafides_migrator;

-- Name: auth_throttles auth_throttles_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.auth_throttles
    ADD CONSTRAINT auth_throttles_pkey PRIMARY KEY (scope);

-- Name: auth_throttles; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.auth_throttles (
    scope text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    window_started_at timestamp with time zone DEFAULT now() NOT NULL,
    blocked_until timestamp with time zone,
    CONSTRAINT auth_throttles_attempts_check CHECK ((attempts >= 0))
);


ALTER TABLE bonafides.auth_throttles OWNER TO bonafides_migrator;

-- Name: backup_daily_runs backup_daily_runs_generation_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.backup_daily_runs
    ADD CONSTRAINT backup_daily_runs_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES bonafides.installation_backups(id) ON DELETE SET NULL;

-- Name: backup_daily_runs backup_daily_runs_namespace_id_slot_date_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.backup_daily_runs
    ADD CONSTRAINT backup_daily_runs_namespace_id_slot_date_key UNIQUE (namespace_id, slot_date);

-- Name: backup_daily_runs backup_daily_runs_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.backup_daily_runs
    ADD CONSTRAINT backup_daily_runs_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: backup_daily_runs backup_daily_runs_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.backup_daily_runs
    ADD CONSTRAINT backup_daily_runs_pkey PRIMARY KEY (id);

-- Name: backup_daily_runs; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.backup_daily_runs (
    id uuid NOT NULL,
    owner_id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    slot_date date NOT NULL,
    config_sha256 text NOT NULL,
    storage_sha256 text NOT NULL,
    generation_id uuid,
    state text NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_at timestamp with time zone,
    copied_at timestamp with time zone,
    pruned_at timestamp with time zone,
    finished_at timestamp with time zone,
    failure_code text,
    CONSTRAINT backup_daily_runs_check CHECK (((state <> ALL (ARRAY['failed'::text, 'pruned'::text, 'disabled'::text])) OR (finished_at IS NOT NULL))),
    CONSTRAINT backup_daily_runs_check1 CHECK (((state <> ALL (ARRAY['created'::text, 'copied'::text, 'pruned'::text])) OR (created_at IS NOT NULL))),
    CONSTRAINT backup_daily_runs_check2 CHECK (((state <> 'copied'::text) OR (copied_at IS NOT NULL))),
    CONSTRAINT backup_daily_runs_config_sha256_check CHECK ((config_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backup_daily_runs_failure_code_check CHECK ((failure_code = ANY (ARRAY['backup_schedule_capture_failed'::text, 'backup_schedule_copy_failed'::text, 'backup_schedule_retention_failed'::text, 'backup_schedule_interrupted'::text, 'backup_schedule_restored_disabled'::text]))),
    CONSTRAINT backup_daily_runs_state_check CHECK ((state = ANY (ARRAY['running'::text, 'created'::text, 'copied'::text, 'failed'::text, 'pruned'::text, 'disabled'::text]))),
    CONSTRAINT backup_daily_runs_storage_sha256_check CHECK ((storage_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.backup_daily_runs OWNER TO bonafides_migrator;

-- Name: backup_daily_runs_active; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX backup_daily_runs_active ON bonafides.backup_daily_runs USING btree (namespace_id) WHERE (finished_at IS NULL);

-- Name: backup_daily_runs_retention; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX backup_daily_runs_retention ON bonafides.backup_daily_runs USING btree (namespace_id, storage_sha256, slot_date DESC);

-- Name: begin_guarded_installation_control(uuid, uuid, uuid, bigint, uuid, uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.begin_guarded_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid, p_guard uuid) RETURNS SETOF bonafides.installation_controls
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 PERFORM bonafides.bind_execution_guard(p_guard);
 SELECT * INTO STRICT c FROM bonafides.begin_installation_control(
  p_owner,p_workflow,p_holder,p_workflow_fence,p_control_holder);
 UPDATE bonafides.installation_controls SET execution_guard_id=p_guard WHERE id=c.id;
 RETURN QUERY SELECT * FROM bonafides.installation_controls WHERE id=c.id;
END $$;


ALTER FUNCTION bonafides.begin_guarded_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid, p_guard uuid) OWNER TO bonafides_migrator;

-- Name: begin_installation_control(uuid, uuid, uuid, bigint, uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.begin_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid) RETURNS SETOF bonafides.installation_controls
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE
 i bonafides.installation_state%ROWTYPE;
 w bonafides.workflow_runs%ROWTYPE;
 a bonafides.action_approvals%ROWTYPE;
 v_deadline timestamptz;
BEGIN
 SELECT * INTO STRICT i FROM bonafides.installation_state WHERE singleton FOR UPDATE;
 PERFORM bonafides.assert_workflow_fence(p_owner,p_workflow,p_holder,p_workflow_fence);
 SELECT * INTO STRICT w FROM bonafides.workflow_runs
 WHERE owner_id=p_owner AND id=p_workflow FOR UPDATE;
 IF w.kind NOT IN ('portable_export','portable_import','privacy_delete','backup','restore_validate')
 OR p_control_holder=p_holder THEN
  RAISE EXCEPTION 'invalid installation control' USING ERRCODE='55000';
 END IF;
 IF w.kind IN ('portable_import','privacy_delete','restore_validate') THEN
  SELECT * INTO STRICT a FROM bonafides.action_approvals
  WHERE owner_id=p_owner AND workflow_run_id=p_workflow AND intent IS NOT NULL FOR UPDATE;
  IF a.approved_at IS NULL OR a.consumed_at IS NULL OR a.revoked_at IS NOT NULL
  OR a.expires_at<=clock_timestamp()
  OR a.action_kind<>(CASE WHEN w.kind='privacy_delete' THEN 'privacy_delete' ELSE 'replace_workspace' END)
  OR (a.intent->>'namespace_id')::uuid IS DISTINCT FROM i.namespace_id
  OR (a.intent->>'maintenance_epoch')::bigint IS DISTINCT FROM i.maintenance_epoch
  OR a.target_version IS DISTINCT FROM (
   SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=p_owner
  ) THEN
   RAISE EXCEPTION 'unapproved installation control' USING ERRCODE='55000';
  END IF;
 END IF;
 v_deadline:=least(w.deadline_at,clock_timestamp()+interval '1 hour');
 UPDATE bonafides.installation_state SET maintenance_active=true,
  maintenance_epoch=maintenance_epoch+1 WHERE singleton;
 UPDATE bonafides.workflow_runs SET fence=fence+1,lease_holder=p_control_holder,
  lease_expires_at=least(v_deadline,clock_timestamp()+interval '30 seconds'),
  version=version+1,updated_at=clock_timestamp() WHERE owner_id=p_owner AND id=p_workflow;
 INSERT INTO bonafides.installation_controls(
  id,owner_id,holder,fence,workflow_fence,namespace_id,maintenance_epoch,purpose,
  approval_id,scope,deadline_at,drain_deadline_at,lease_expires_at
 ) VALUES(
  p_workflow,p_owner,p_control_holder,1,p_workflow_fence+1,i.namespace_id,i.maintenance_epoch+1,w.kind,
  a.id,CASE WHEN a.id IS NULL THEN w.input ELSE a.intent END,v_deadline,
  least(v_deadline,clock_timestamp()+interval '60 seconds'),
  least(v_deadline,clock_timestamp()+interval '30 seconds')
 );
 RETURN QUERY SELECT * FROM bonafides.installation_controls WHERE id=p_workflow;
END $$;


ALTER FUNCTION bonafides.begin_installation_control(p_owner uuid, p_workflow uuid, p_holder uuid, p_workflow_fence bigint, p_control_holder uuid) OWNER TO bonafides_migrator;

-- Name: begin_maintenance_recovery(uuid, uuid, bigint, uuid, uuid, bigint, uuid, text, text, uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.begin_maintenance_recovery(p_recovery uuid, p_namespace uuid, p_epoch bigint, p_control uuid, p_previous_holder uuid, p_previous_fence bigint, p_guard uuid, p_parser text, p_renderer text, p_holder uuid) RETURNS SETOF bonafides.installation_controls
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE; v_count bigint; v_deadline timestamptz;
BEGIN
 PERFORM bonafides.inspect_maintenance_recovery(p_namespace,p_epoch,p_control,p_previous_holder,p_previous_fence,p_guard);
 IF p_holder IS NULL OR p_holder=p_previous_holder THEN
  RAISE EXCEPTION 'fresh recovery holder required' USING ERRCODE='55000';
 END IF;
 v_deadline:=clock_timestamp()+interval '14 minutes';
 SELECT count(*) INTO v_count FROM bonafides.installation_activities;
 UPDATE bonafides.maintenance_recoveries SET outcome='SUPERSEDED',finished_at=clock_timestamp()
 WHERE namespace_id=p_namespace AND outcome IS NULL;
 IF p_control IS NOT NULL THEN
  UPDATE bonafides.workflow_runs SET state='running',cancel_requested_at=NULL,
   fence=fence+1,lease_holder=p_holder,lease_expires_at=clock_timestamp()+interval '30 seconds',
   deadline_at=v_deadline,finished_at=NULL,version=version+1,updated_at=clock_timestamp()
  WHERE id=p_control;
  UPDATE bonafides.installation_controls SET holder=p_holder,fence=fence+1,
   workflow_fence=(SELECT fence FROM bonafides.workflow_runs WHERE id=p_control),
   phase='quiescent',settled_at=NULL,deadline_at=v_deadline,
   drain_deadline_at=clock_timestamp()+interval '60 seconds',
   lease_expires_at=clock_timestamp()+interval '30 seconds'
  WHERE id=p_control RETURNING * INTO c;
 END IF;
 INSERT INTO bonafides.maintenance_recoveries(id,namespace_id,maintenance_epoch,control_id,
  previous_holder,previous_fence,holder,control_fence,execution_guard_id,parser_nonce,renderer_nonce,
  process_proof,cleared_activities,deadline_at)
 VALUES(p_recovery,p_namespace,p_epoch,p_control,p_previous_holder,p_previous_fence,p_holder,c.fence,
  p_guard,p_parser,p_renderer,'exclusive-kernel-flock/v1',v_count,v_deadline);
 DELETE FROM bonafides.installation_activities;
 UPDATE bonafides.installation_state SET maintenance_active=true WHERE singleton;
 IF p_control IS NOT NULL THEN RETURN NEXT c; END IF;
END $$;


ALTER FUNCTION bonafides.begin_maintenance_recovery(p_recovery uuid, p_namespace uuid, p_epoch bigint, p_control uuid, p_previous_holder uuid, p_previous_fence bigint, p_guard uuid, p_parser text, p_renderer text, p_holder uuid) OWNER TO bonafides_migrator;

-- Name: bind_execution_guard(uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.bind_execution_guard(p_guard uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE i bonafides.installation_state%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM bonafides.installation_state WHERE singleton;
 IF p_guard IS NULL OR (i.execution_guard_id IS NOT NULL AND i.execution_guard_id<>p_guard) THEN
  RAISE EXCEPTION 'execution guard identity mismatch' USING ERRCODE='55000';
 END IF;
 IF i.execution_guard_id IS NULL THEN
  UPDATE bonafides.installation_state SET execution_guard_id=p_guard
  WHERE singleton AND (execution_guard_id IS NULL OR execution_guard_id=p_guard);
  IF NOT FOUND THEN
   RAISE EXCEPTION 'execution guard identity mismatch' USING ERRCODE='55000';
  END IF;
 END IF;
END $$;


ALTER FUNCTION bonafides.bind_execution_guard(p_guard uuid) OWNER TO bonafides_migrator;

-- Name: blobs blobs_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.blobs
    ADD CONSTRAINT blobs_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: blobs blobs_owner_id_sha256_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.blobs
    ADD CONSTRAINT blobs_owner_id_sha256_key UNIQUE (owner_id, sha256);

-- Name: blobs blobs_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.blobs
    ADD CONSTRAINT blobs_pkey PRIMARY KEY (owner_id, id);

-- Name: blobs; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.blobs (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    media_type text NOT NULL,
    storage_key text NOT NULL,
    state text DEFAULT 'ready'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    CONSTRAINT blobs_byte_length_check CHECK (((byte_length >= 0) AND (byte_length <= 1073741824))),
    CONSTRAINT blobs_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT blobs_state_check CHECK ((state = ANY (ARRAY['staged'::text, 'ready'::text, 'deleting'::text, 'missing'::text]))),
    CONSTRAINT blobs_storage_key_check CHECK ((storage_key ~ '^[0-9a-f]{2}/[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.blobs OWNER TO bonafides_migrator;

-- Name: blobs_gc_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX blobs_gc_idx ON bonafides.blobs USING btree (owner_id, state, expires_at);

-- Name: bonafides; Type: SCHEMA; Schema: -; Owner: bonafides_migrator

CREATE SCHEMA bonafides;


ALTER SCHEMA bonafides OWNER TO bonafides_migrator;

-- Name: bonafides_migrations; Type: SCHEMA; Schema: -; Owner: bonafides_migrator

CREATE SCHEMA bonafides_migrations;


ALTER SCHEMA bonafides_migrations OWNER TO bonafides_migrator;

-- Name: check_observation_source(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.check_observation_source() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM job_postings p JOIN source_configs s
 ON s.owner_id=p.owner_id AND s.id=p.source_config_id
 WHERE p.owner_id=NEW.owner_id AND p.id=NEW.posting_id AND p.source_config_id=NEW.source_config_id
 AND p.external_id=NEW.external_id AND (s.source_kind='manual' OR NEW.policy_review_id IS NOT NULL))
 THEN RAISE EXCEPTION 'observation attribution mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.check_observation_source() OWNER TO bonafides_migrator;

-- Name: check_source_review(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.check_source_review() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM source_configs s WHERE s.owner_id=NEW.owner_id AND s.id=NEW.id
 AND s.source_kind='greenhouse' AND s.current_policy_review_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM source_policy_reviews r WHERE r.owner_id=s.owner_id AND r.id=s.current_policy_review_id
 AND r.source_config_id=s.id AND r.policy_version=s.policy_version AND r.board_token=s.board_token
 AND r.company=s.company AND r.terms_url=s.terms_url AND r.terms_revision=s.terms_revision
 AND r.allowed_host=s.allowed_host AND r.request_limit_per_minute=s.request_limit_per_minute
 AND r.reviewed_at=s.terms_reviewed_at))
 THEN RAISE EXCEPTION 'source review mismatch' USING ERRCODE='55000'; END IF;
 RETURN NULL;
END $$;


ALTER FUNCTION bonafides.check_source_review() OWNER TO bonafides_migrator;

-- Name: claim_evidence_links claim_evidence_links_owner_id_claim_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_evidence_links
    ADD CONSTRAINT claim_evidence_links_owner_id_claim_revision_id_fkey FOREIGN KEY (owner_id, claim_revision_id) REFERENCES bonafides.claim_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: claim_evidence_links claim_evidence_links_owner_id_evidence_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_evidence_links
    ADD CONSTRAINT claim_evidence_links_owner_id_evidence_revision_id_fkey FOREIGN KEY (owner_id, evidence_revision_id) REFERENCES bonafides.evidence_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: claim_evidence_links claim_evidence_links_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_evidence_links
    ADD CONSTRAINT claim_evidence_links_pkey PRIMARY KEY (owner_id, claim_revision_id, evidence_revision_id);

-- Name: claim_evidence_links immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.claim_evidence_links FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: claim_evidence_links; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.claim_evidence_links (
    owner_id uuid NOT NULL,
    claim_revision_id uuid NOT NULL,
    evidence_revision_id uuid NOT NULL,
    support_kind text NOT NULL,
    excerpt text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT claim_evidence_links_support_kind_check CHECK ((support_kind = ANY (ARRAY['direct'::text, 'user_asserted'::text, 'context'::text])))
);


ALTER TABLE bonafides.claim_evidence_links OWNER TO bonafides_migrator;

-- Name: claim_profile_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX claim_profile_idx ON bonafides.professional_claims USING btree (owner_id, profile_id);

-- Name: claim_revisions claim_revisions_owner_id_claim_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_revisions
    ADD CONSTRAINT claim_revisions_owner_id_claim_id_fkey FOREIGN KEY (owner_id, claim_id) REFERENCES bonafides.professional_claims(owner_id, id) ON DELETE CASCADE;

-- Name: claim_revisions claim_revisions_owner_id_claim_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_revisions
    ADD CONSTRAINT claim_revisions_owner_id_claim_id_id_key UNIQUE (owner_id, claim_id, id);

-- Name: claim_revisions claim_revisions_owner_id_claim_id_revision_no_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_revisions
    ADD CONSTRAINT claim_revisions_owner_id_claim_id_revision_no_key UNIQUE (owner_id, claim_id, revision_no);

-- Name: claim_revisions claim_revisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.claim_revisions
    ADD CONSTRAINT claim_revisions_pkey PRIMARY KEY (owner_id, id);

-- Name: claim_revisions immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.claim_revisions FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: claim_revisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.claim_revisions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    claim_id uuid NOT NULL,
    revision_no bigint NOT NULL,
    payload jsonb NOT NULL,
    content_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT claim_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT claim_revisions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT claim_revisions_revision_no_check CHECK ((revision_no > 0))
);


ALTER TABLE bonafides.claim_revisions OWNER TO bonafides_migrator;

-- Name: clear_erased_portable_previews(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.clear_erased_portable_previews() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
BEGIN
 UPDATE bonafides.portable_previews SET candidate_bytes=NULL,
  cleared_at=clock_timestamp(),clear_reason='erased'
 WHERE owner_id=NEW.owner_id AND cleared_at IS NULL;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.clear_erased_portable_previews() OWNER TO bonafides_migrator;

-- Name: clear_portable_preview_chunks(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.clear_portable_preview_chunks() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
BEGIN
 DELETE FROM bonafides.portable_preview_chunks WHERE owner_id=NEW.owner_id AND preview_id=NEW.id;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.clear_portable_preview_chunks() OWNER TO bonafides_migrator;

-- Name: complete_portable_purge(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.complete_portable_purge(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR EXISTS(SELECT 1 FROM bonafides.portable_purge_items WHERE control_id=c.id AND resolved_at IS NULL) THEN
  RAISE EXCEPTION 'live purge incomplete' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM bonafides.portable_purges
  WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NOT NULL) THEN
  RETURN;
 END IF;
 UPDATE bonafides.portable_purges SET completed_at=clock_timestamp()
 WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NULL;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'purge receipt required' USING ERRCODE='55000';
 END IF;
END $$;


ALTER FUNCTION bonafides.complete_portable_purge(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: contacts_application_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX contacts_application_idx ON bonafides.application_contacts USING btree (owner_id, application_id, created_at, id);

-- Name: credential_key_usage credential_key_usage_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.credential_key_usage
    ADD CONSTRAINT credential_key_usage_pkey PRIMARY KEY (key_id);

-- Name: credential_key_usage; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.credential_key_usage (
    key_id text NOT NULL,
    encryption_count bigint NOT NULL,
    CONSTRAINT credential_key_usage_encryption_count_check CHECK (((encryption_count >= 0) AND (encryption_count <= 1048576)))
);


ALTER TABLE bonafides.credential_key_usage OWNER TO bonafides_migrator;

-- Name: credentials_account_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX credentials_account_idx ON bonafides.encrypted_credentials USING btree (owner_id, external_account_id);

-- Name: doc_artifacts_blob_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX doc_artifacts_blob_idx ON bonafides.document_artifacts USING btree (owner_id, blob_id);

-- Name: doc_links_claim_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX doc_links_claim_idx ON bonafides.document_claim_links USING btree (owner_id, claim_revision_id);

-- Name: doc_links_evidence_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX doc_links_evidence_idx ON bonafides.document_claim_links USING btree (owner_id, evidence_revision_id);

-- Name: doc_revisions_model_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX doc_revisions_model_idx ON bonafides.document_revisions USING btree (owner_id, model_run_id);

-- Name: doc_revisions_profile_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX doc_revisions_profile_idx ON bonafides.document_revisions USING btree (owner_id, profile_revision_id);

-- Name: document_artifacts document_artifacts_owner_id_blob_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_artifacts
    ADD CONSTRAINT document_artifacts_owner_id_blob_id_fkey FOREIGN KEY (owner_id, blob_id) REFERENCES bonafides.blobs(owner_id, id) ON DELETE RESTRICT;

-- Name: document_artifacts document_artifacts_owner_id_document_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_artifacts
    ADD CONSTRAINT document_artifacts_owner_id_document_revision_id_fkey FOREIGN KEY (owner_id, document_revision_id) REFERENCES bonafides.document_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: document_artifacts document_artifacts_owner_id_document_revision_id_renderer_v_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_artifacts
    ADD CONSTRAINT document_artifacts_owner_id_document_revision_id_renderer_v_key UNIQUE (owner_id, document_revision_id, renderer_version, format);

-- Name: document_artifacts document_artifacts_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_artifacts
    ADD CONSTRAINT document_artifacts_pkey PRIMARY KEY (owner_id, id);

-- Name: document_artifacts; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_artifacts (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_revision_id uuid NOT NULL,
    blob_id uuid NOT NULL,
    renderer_version text NOT NULL,
    format text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_artifacts_format_check CHECK ((format = ANY (ARRAY['pdf'::text, 'docx'::text])))
);


ALTER TABLE bonafides.document_artifacts OWNER TO bonafides_migrator;

-- Name: document_claim_links document_claim_links_owner_id_claim_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_claim_links
    ADD CONSTRAINT document_claim_links_owner_id_claim_revision_id_fkey FOREIGN KEY (owner_id, claim_revision_id) REFERENCES bonafides.claim_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: document_claim_links document_claim_links_owner_id_document_revision_id_claim_pa_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_claim_links
    ADD CONSTRAINT document_claim_links_owner_id_document_revision_id_claim_pa_key UNIQUE (owner_id, document_revision_id, claim_path, claim_revision_id, evidence_revision_id);

-- Name: document_claim_links document_claim_links_owner_id_document_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_claim_links
    ADD CONSTRAINT document_claim_links_owner_id_document_revision_id_fkey FOREIGN KEY (owner_id, document_revision_id) REFERENCES bonafides.document_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: document_claim_links document_claim_links_owner_id_evidence_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_claim_links
    ADD CONSTRAINT document_claim_links_owner_id_evidence_revision_id_fkey FOREIGN KEY (owner_id, evidence_revision_id) REFERENCES bonafides.evidence_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: document_claim_links document_claim_links_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_claim_links
    ADD CONSTRAINT document_claim_links_pkey PRIMARY KEY (owner_id, id);

-- Name: document_claim_links immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.document_claim_links FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: document_claim_links; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_claim_links (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_revision_id uuid NOT NULL,
    claim_path text NOT NULL,
    claim_revision_id uuid,
    evidence_revision_id uuid,
    excerpt text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_claim_links_check CHECK (((status <> 'supported'::text) OR ((claim_revision_id IS NOT NULL) AND (evidence_revision_id IS NOT NULL)))),
    CONSTRAINT document_claim_links_status_check CHECK ((status = ANY (ARRAY['supported'::text, 'unsupported'::text, 'user_asserted'::text])))
);


ALTER TABLE bonafides.document_claim_links OWNER TO bonafides_migrator;

-- Name: document_drafts document_drafts_owner_id_application_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_owner_id_application_id_fkey FOREIGN KEY (owner_id, application_id) REFERENCES bonafides.applications(owner_id, id) ON DELETE CASCADE;

-- Name: document_drafts document_drafts_owner_id_application_id_kind_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_owner_id_application_id_kind_key UNIQUE (owner_id, application_id, kind);

-- Name: document_drafts document_drafts_owner_id_id_current_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_owner_id_id_current_revision_id_fkey FOREIGN KEY (owner_id, id, current_revision_id) REFERENCES bonafides.document_revisions(owner_id, document_draft_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: document_drafts document_drafts_owner_id_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_owner_id_profile_revision_id_fkey FOREIGN KEY (owner_id, profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: document_drafts document_drafts_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_pkey PRIMARY KEY (owner_id, id);

-- Name: document_drafts document_drafts_scope_identity_unique; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_drafts
    ADD CONSTRAINT document_drafts_scope_identity_unique UNIQUE (owner_id, id, scope);

-- Name: document_drafts; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_drafts (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid,
    kind text NOT NULL,
    profile_revision_id uuid NOT NULL,
    current_revision_id uuid,
    version bigint DEFAULT 1 NOT NULL,
    stale_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    scope text DEFAULT 'tailored'::text NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT document_drafts_kind_check CHECK ((kind = ANY (ARRAY['resume'::text, 'cover_letter'::text]))),
    CONSTRAINT document_drafts_scope_check CHECK ((scope = ANY (ARRAY['base'::text, 'tailored'::text]))),
    CONSTRAINT document_drafts_scope_parent_check CHECK ((((scope = 'base'::text) AND (application_id IS NULL) AND (kind = 'resume'::text)) OR ((scope = 'tailored'::text) AND (application_id IS NOT NULL)))),
    CONSTRAINT document_drafts_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.document_drafts OWNER TO bonafides_migrator;

-- Name: document_regeneration_proposal_history_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX document_regeneration_proposal_history_idx ON bonafides.document_regeneration_proposals USING btree (owner_id, document_draft_id, created_at DESC, id DESC);

-- Name: document_regeneration_proposals document_regeneration_proposa_owner_id_document_draft_id_t_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposa_owner_id_document_draft_id_t_fkey FOREIGN KEY (owner_id, document_draft_id, target_revision_id) REFERENCES bonafides.document_revisions(owner_id, document_draft_id, id) ON DELETE RESTRICT;

-- Name: document_regeneration_proposals document_regeneration_proposa_owner_id_historical_model_ru_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposa_owner_id_historical_model_ru_fkey FOREIGN KEY (owner_id, historical_model_run_id) REFERENCES bonafides.portable_model_history(owner_id, id) ON DELETE RESTRICT;

-- Name: document_regeneration_proposals document_regeneration_proposa_owner_id_model_run_id_workfl_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposa_owner_id_model_run_id_workfl_fkey FOREIGN KEY (owner_id, model_run_id, workflow_run_id) REFERENCES bonafides.model_runs(owner_id, id, workflow_run_id) ON DELETE RESTRICT;

-- Name: document_regeneration_proposals document_regeneration_proposals_owner_id_document_draft_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposals_owner_id_document_draft_id_fkey FOREIGN KEY (owner_id, document_draft_id) REFERENCES bonafides.document_drafts(owner_id, id) ON DELETE RESTRICT;

-- Name: document_regeneration_proposals document_regeneration_proposals_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposals_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: document_regeneration_proposals document_regeneration_proposals_owner_id_workflow_run_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposals_owner_id_workflow_run_id_key UNIQUE (owner_id, workflow_run_id);

-- Name: document_regeneration_proposals document_regeneration_proposals_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_regeneration_proposals
    ADD CONSTRAINT document_regeneration_proposals_pkey PRIMARY KEY (owner_id, id);

-- Name: document_regeneration_proposals; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_regeneration_proposals (
    owner_id uuid NOT NULL,
    id uuid NOT NULL,
    document_draft_id uuid NOT NULL,
    target_revision_id uuid NOT NULL,
    target_content_sha256 text NOT NULL,
    target_document_version bigint CONSTRAINT document_regeneration_proposal_target_document_version_not_null NOT NULL,
    section_key text NOT NULL,
    original_section jsonb NOT NULL,
    proposed_section jsonb NOT NULL,
    validation_issues jsonb NOT NULL,
    model_run_id uuid,
    workflow_run_id uuid,
    model_run jsonb NOT NULL,
    context_snapshot jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    historical_model_run_id uuid,
    CONSTRAINT document_regeneration_proposals_check CHECK ((((model_run_id IS NOT NULL) AND (workflow_run_id IS NOT NULL) AND (historical_model_run_id IS NULL)) OR ((model_run_id IS NULL) AND (workflow_run_id IS NULL) AND (historical_model_run_id IS NOT NULL)))),
    CONSTRAINT document_regeneration_proposals_context_snapshot_check CHECK ((jsonb_typeof(context_snapshot) = 'object'::text)),
    CONSTRAINT document_regeneration_proposals_model_run_check CHECK ((jsonb_typeof(model_run) = 'object'::text)),
    CONSTRAINT document_regeneration_proposals_original_section_check CHECK ((jsonb_typeof(original_section) = 'object'::text)),
    CONSTRAINT document_regeneration_proposals_proposed_section_check CHECK ((jsonb_typeof(proposed_section) = 'object'::text)),
    CONSTRAINT document_regeneration_proposals_section_key_check CHECK ((section_key ~ '^[a-zA-Z0-9_-]{1,80}$'::text)),
    CONSTRAINT document_regeneration_proposals_target_content_sha256_check CHECK ((target_content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT document_regeneration_proposals_target_document_version_check CHECK ((target_document_version > 0)),
    CONSTRAINT document_regeneration_proposals_validation_issues_check CHECK ((jsonb_typeof(validation_issues) = 'array'::text))
);


ALTER TABLE bonafides.document_regeneration_proposals OWNER TO bonafides_migrator;

-- Name: document_reviews document_reviews_owner_id_document_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_reviews
    ADD CONSTRAINT document_reviews_owner_id_document_revision_id_fkey FOREIGN KEY (owner_id, document_revision_id) REFERENCES bonafides.document_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: document_reviews document_reviews_owner_id_document_revision_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_reviews
    ADD CONSTRAINT document_reviews_owner_id_document_revision_id_key UNIQUE (owner_id, document_revision_id);

-- Name: document_reviews document_reviews_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_reviews
    ADD CONSTRAINT document_reviews_pkey PRIMARY KEY (owner_id, id);

-- Name: document_reviews immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.document_reviews FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: document_reviews; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_reviews (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_revision_id uuid NOT NULL,
    decision text NOT NULL,
    reviewed_content_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_block_keys jsonb,
    CONSTRAINT document_reviews_decision_check CHECK ((decision = ANY (ARRAY['accepted'::text, 'changes_requested'::text]))),
    CONSTRAINT document_reviews_reviewed_block_keys_check CHECK (((reviewed_block_keys IS NULL) OR (jsonb_typeof(reviewed_block_keys) = 'array'::text))),
    CONSTRAINT document_reviews_reviewed_content_sha256_check CHECK ((reviewed_content_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.document_reviews OWNER TO bonafides_migrator;

-- Name: document_revision_historical_model_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX document_revision_historical_model_idx ON bonafides.document_revisions USING btree (owner_id, historical_model_run_id) WHERE (historical_model_run_id IS NOT NULL);

-- Name: document_revision_history_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX document_revision_history_idx ON bonafides.document_revisions USING btree (owner_id, document_draft_id, revision_no DESC);

-- Name: document_revisions document_revisions_owner_id_document_draft_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_document_draft_id_fkey FOREIGN KEY (owner_id, document_draft_id) REFERENCES bonafides.document_drafts(owner_id, id) ON DELETE CASCADE;

-- Name: document_revisions document_revisions_owner_id_document_draft_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_document_draft_id_id_key UNIQUE (owner_id, document_draft_id, id);

-- Name: document_revisions document_revisions_owner_id_document_draft_id_revision_no_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_document_draft_id_revision_no_key UNIQUE (owner_id, document_draft_id, revision_no);

-- Name: document_revisions document_revisions_owner_id_historical_model_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_historical_model_run_id_fkey FOREIGN KEY (owner_id, historical_model_run_id) REFERENCES bonafides.portable_model_history(owner_id, id) ON DELETE RESTRICT;

-- Name: document_revisions document_revisions_owner_id_model_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_model_run_id_fkey FOREIGN KEY (owner_id, model_run_id) REFERENCES bonafides.model_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: document_revisions document_revisions_owner_id_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_owner_id_profile_revision_id_fkey FOREIGN KEY (owner_id, profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: document_revisions document_revisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_pkey PRIMARY KEY (owner_id, id);

-- Name: document_revisions document_revisions_scope_fk; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.document_revisions
    ADD CONSTRAINT document_revisions_scope_fk FOREIGN KEY (owner_id, document_draft_id, scope) REFERENCES bonafides.document_drafts(owner_id, id, scope) ON DELETE CASCADE;

-- Name: document_revisions immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.document_revisions FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: document_revisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.document_revisions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_draft_id uuid NOT NULL,
    revision_no bigint NOT NULL,
    profile_revision_id uuid NOT NULL,
    model_run_id uuid,
    body jsonb NOT NULL,
    posting_snapshot jsonb,
    provenance_snapshot jsonb NOT NULL,
    template_version text NOT NULL,
    content_sha256 text NOT NULL,
    origin text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    scope text DEFAULT 'tailored'::text NOT NULL,
    validation_issues jsonb DEFAULT '[]'::jsonb NOT NULL,
    historical_model_run_id uuid,
    CONSTRAINT document_revisions_base_context_check CHECK ((((scope = 'base'::text) AND (posting_snapshot IS NULL) AND (model_run_id IS NULL) AND (origin = ANY (ARRAY['deterministic'::text, 'manual'::text, 'restore'::text]))) OR ((scope = 'tailored'::text) AND (posting_snapshot IS NOT NULL)))),
    CONSTRAINT document_revisions_body_check CHECK ((jsonb_typeof(body) = 'object'::text)),
    CONSTRAINT document_revisions_check CHECK (((model_run_id IS NULL) OR (historical_model_run_id IS NULL))),
    CONSTRAINT document_revisions_check1 CHECK (((scope <> 'base'::text) OR (historical_model_run_id IS NULL))),
    CONSTRAINT document_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT document_revisions_origin_check CHECK ((origin = ANY (ARRAY['generated'::text, 'deterministic'::text, 'manual'::text, 'restore'::text]))),
    CONSTRAINT document_revisions_posting_snapshot_check CHECK ((jsonb_typeof(posting_snapshot) = 'object'::text)),
    CONSTRAINT document_revisions_provenance_snapshot_check CHECK ((jsonb_typeof(provenance_snapshot) = 'object'::text)),
    CONSTRAINT document_revisions_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT document_revisions_scope_check CHECK ((scope = ANY (ARRAY['base'::text, 'tailored'::text]))),
    CONSTRAINT document_revisions_validation_issues_check CHECK ((jsonb_typeof(validation_issues) = 'array'::text))
);


ALTER TABLE bonafides.document_revisions OWNER TO bonafides_migrator;

-- Name: drafts_profile_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX drafts_profile_idx ON bonafides.document_drafts USING btree (owner_id, profile_revision_id);

-- Name: duplicate_decision_reverse; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX duplicate_decision_reverse ON bonafides.posting_duplicate_decisions USING btree (owner_id, candidate_id, posting_id);

-- Name: encrypted_credentials encrypted_credentials_key_id_nonce_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.encrypted_credentials
    ADD CONSTRAINT encrypted_credentials_key_id_nonce_key UNIQUE (key_id, nonce);

-- Name: encrypted_credentials encrypted_credentials_owner_id_external_account_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.encrypted_credentials
    ADD CONSTRAINT encrypted_credentials_owner_id_external_account_id_fkey FOREIGN KEY (owner_id, external_account_id) REFERENCES bonafides.external_accounts(owner_id, id) ON DELETE CASCADE;

-- Name: encrypted_credentials encrypted_credentials_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.encrypted_credentials
    ADD CONSTRAINT encrypted_credentials_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: encrypted_credentials encrypted_credentials_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.encrypted_credentials
    ADD CONSTRAINT encrypted_credentials_pkey PRIMARY KEY (owner_id, id);

-- Name: encrypted_credentials; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.encrypted_credentials (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    external_account_id uuid,
    purpose text NOT NULL,
    key_id text NOT NULL,
    algorithm text NOT NULL,
    nonce bytea NOT NULL,
    ciphertext bytea NOT NULL,
    aad_version integer DEFAULT 1 NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    rotated_at timestamp with time zone,
    provider_origin text,
    CONSTRAINT encrypted_credentials_aad_version_check CHECK ((aad_version = ANY (ARRAY[1, 2]))),
    CONSTRAINT encrypted_credentials_algorithm_check CHECK ((algorithm = 'AES-256-GCM'::text)),
    CONSTRAINT encrypted_credentials_ciphertext_check CHECK ((octet_length(ciphertext) >= 16)),
    CONSTRAINT encrypted_credentials_nonce_check CHECK ((octet_length(nonce) = 12)),
    CONSTRAINT encrypted_credentials_origin_binding CHECK ((((aad_version = 1) AND (provider_origin IS NULL)) OR ((aad_version = 2) AND (provider_origin IS NOT NULL) AND (length(provider_origin) <= 2048) AND (provider_origin ~ '^https://([a-z0-9][a-z0-9.-]*|\[[0-9a-f:]+\])(:[0-9]{1,5})?$'::text)))),
    CONSTRAINT encrypted_credentials_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.encrypted_credentials OWNER TO bonafides_migrator;

-- Name: erase_portable_scope(uuid, uuid, bigint, uuid, jsonb, jsonb); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.erase_portable_scope(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid DEFAULT NULL::uuid, p_runtime_pins jsonb DEFAULT '{}'::jsonb, p_orphans jsonb DEFAULT '[]'::jsonb) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $_$
DECLARE
 c bonafides.installation_controls%ROWTYPE;
 v_application uuid;
 v_workspace boolean;
 v_documents uuid[];
 v_revisions uuid[];
 v_ids uuid[];
 v_workflows uuid[];
 v_models uuid[];
 v_blobs uuid[];
 v_history uuid[];
 v_item jsonb;
 v_table text;
 v_count bigint;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose NOT IN ('privacy_delete','portable_import')
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id) THEN
  RAISE EXCEPTION 'portable erasure refused' USING ERRCODE='55000';
 END IF;
 v_workspace:=c.purpose='portable_import' OR coalesce(c.scope->'payload'->'categories'='["all_workspace"]'::jsonb,false);
 IF NOT v_workspace THEN
  IF c.scope->'payload'->'categories' IS DISTINCT FROM '["applications"]'::jsonb
   OR c.scope->'payload'->>'application_id' IS NULL THEN
   RAISE EXCEPTION 'unsupported deletion scope' USING ERRCODE='55000';
  END IF;
  v_application:=(c.scope->'payload'->>'application_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM bonafides.applications WHERE owner_id=c.owner_id
   AND id=v_application AND version=(c.scope->'payload'->>'application_version')::bigint) THEN
   RAISE EXCEPTION 'stale application deletion' USING ERRCODE='55000';
  END IF;
 END IF;
 IF c.purpose='portable_import' THEN
  PERFORM bonafides.assert_portable_checkpoint(p_id,p_holder,p_fence,p_checkpoint,p_runtime_pins);
 END IF;
 IF jsonb_typeof(p_orphans) IS DISTINCT FROM 'array' OR jsonb_array_length(p_orphans)>100000
 OR octet_length(p_orphans::text)>33554432 THEN
  RAISE EXCEPTION 'invalid orphan inventory' USING ERRCODE='55000';
 END IF;
 SET CONSTRAINTS ALL DEFERRED;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_documents
 FROM bonafides.document_drafts WHERE owner_id=c.owner_id AND (v_workspace OR application_id=v_application);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_revisions
 FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_ids FROM (
  SELECT unnest(v_documents||v_revisions) AS id
  UNION SELECT v_application WHERE v_application IS NOT NULL
  UNION SELECT id FROM bonafides.application_notes WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_contacts WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.application_stage_events WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.appointments WHERE owner_id=c.owner_id AND application_id=v_application
  UNION SELECT id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)
 ) selected;
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_workflows FROM bonafides.workflow_runs
 WHERE owner_id=c.owner_id AND id<>c.id AND (v_workspace
  OR input->>'application_id'=v_application::text
  OR input->>'document_id' IN (SELECT id::text FROM unnest(v_documents) AS documents(id))
  OR input->>'revision_id' IN (SELECT id::text FROM unnest(v_revisions) AS revisions(id)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_models FROM bonafides.model_runs
 WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR id IN (SELECT model_run_id FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT model_run_id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_history FROM bonafides.portable_model_history
 WHERE owner_id=c.owner_id AND (v_workspace OR
  id IN (SELECT historical_model_run_id FROM bonafides.document_revisions WHERE owner_id=c.owner_id AND id=ANY(v_revisions))
  OR id IN (SELECT historical_model_run_id FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents)));
 SELECT coalesce(array_agg(id),'{}'::uuid[]) INTO v_blobs FROM bonafides.blobs WHERE owner_id=c.owner_id AND (v_workspace
  OR id IN (SELECT blob_id FROM bonafides.application_attachments WHERE owner_id=c.owner_id AND application_id=v_application)
  OR id IN (SELECT blob_id FROM bonafides.document_artifacts WHERE owner_id=c.owner_id AND document_revision_id=ANY(v_revisions)));

 INSERT INTO bonafides.portable_purges(control_id,owner_id,scope,application_id)
 VALUES(c.id,c.owner_id,CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,v_application);
 INSERT INTO bonafides.erased_request_keys(owner_id,route_sha256,key_sha256,request_sha256)
 SELECT owner_id,encode(sha256(convert_to(route_key,'UTF8')),'hex'),encode(sha256(convert_to(key,'UTF8')),'hex'),request_sha256
 FROM bonafides.idempotency_records WHERE owner_id=c.owner_id AND (v_workspace OR workflow_run_id=ANY(v_workflows)
  OR EXISTS(
   SELECT 1 FROM jsonb_path_query(response_body,'$.**') AS node(value)
   CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(node.value)='object'
    THEN node.value ELSE '{}'::jsonb END) AS field
   WHERE field.key IN ('id','application_id','document_id','document_revision_id','revision_id',
    'target_revision_id','workflow_id','workflow_run_id','proposal_id','artifact_id')
    AND field.value IN (SELECT to_jsonb(referenced.id::text) FROM unnest(v_ids||v_workflows) AS referenced(id))))
 ON CONFLICT DO NOTHING;
 DELETE FROM bonafides.idempotency_records r WHERE r.owner_id=c.owner_id AND EXISTS(
  SELECT 1 FROM bonafides.erased_request_keys k WHERE k.owner_id=r.owner_id
   AND k.route_sha256=encode(sha256(convert_to(r.route_key,'UTF8')),'hex')
   AND k.key_sha256=encode(sha256(convert_to(r.key,'UTF8')),'hex'));

 -- The controller itself and prior control receipts remain; raw queued inputs,
 -- results and checkpoints do not. A late ordinary executor has a stale fence.
 UPDATE bonafides.workflow_runs SET input='{"erased":true}',result=NULL,
  state=CASE WHEN state IN ('succeeded','cancelled','permanent_failure') THEN state ELSE 'cancelled'::bonafides.workflow_state END,
  cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
  finished_at=coalesce(finished_at,clock_timestamp()),lease_holder=NULL,lease_expires_at=NULL,
  fence=fence+1,version=version+1,updated_at=clock_timestamp(),last_error_code='data_erased'
 WHERE owner_id=c.owner_id AND id=ANY(v_workflows);
 UPDATE bonafides.workflow_steps SET checkpoint='{}',provider_operation_id=NULL
 WHERE owner_id=c.owner_id AND workflow_run_id=ANY(v_workflows);

 -- Break the consent/approval/document provenance cycle only for model rows
 -- being erased. An unexpected cross-scope dependency fails this transaction.
 UPDATE bonafides.model_runs SET hosted_consent_id=NULL WHERE owner_id=c.owner_id AND id=ANY(v_models)
  AND (v_workspace OR (NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id
   AND r.model_run_id=model_runs.id AND NOT r.id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments m WHERE m.owner_id=c.owner_id AND m.model_run_id=model_runs.id)));
 DELETE FROM bonafides.hosted_egress_consents h WHERE h.owner_id=c.owner_id
  AND (v_workspace OR h.workflow_run_id=ANY(v_workflows))
  AND NOT EXISTS(SELECT 1 FROM bonafides.model_runs m WHERE m.owner_id=c.owner_id AND m.hosted_consent_id=h.id);
 DELETE FROM bonafides.action_approvals a WHERE a.owner_id=c.owner_id
  AND (v_workspace OR a.workflow_run_id=ANY(v_workflows) OR a.document_revision_id=ANY(v_revisions))
  AND NOT EXISTS(SELECT 1 FROM bonafides.installation_controls k WHERE k.approval_id=a.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.hosted_egress_consents h WHERE h.owner_id=c.owner_id AND h.approval_id=a.id);
 DELETE FROM bonafides.document_regeneration_proposals WHERE owner_id=c.owner_id AND document_draft_id=ANY(v_documents);
 DELETE FROM bonafides.document_drafts WHERE owner_id=c.owner_id AND id=ANY(v_documents);
 DELETE FROM bonafides.applications WHERE owner_id=c.owner_id AND (v_workspace OR id=v_application);
 IF v_workspace THEN
  DELETE FROM bonafides.match_assessments WHERE owner_id=c.owner_id;
 END IF;
 DELETE FROM bonafides.model_runs m WHERE m.owner_id=c.owner_id AND m.id=ANY(v_models)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id AND r.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments a WHERE a.owner_id=c.owner_id AND a.model_run_id=m.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.model_run_id=m.id);
 DELETE FROM bonafides.portable_model_history h WHERE h.owner_id=c.owner_id AND h.id=ANY(v_history)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_revisions r WHERE r.owner_id=c.owner_id AND r.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.match_assessments a WHERE a.owner_id=c.owner_id AND a.historical_model_run_id=h.id)
  AND NOT EXISTS(SELECT 1 FROM bonafides.document_regeneration_proposals p WHERE p.owner_id=c.owner_id AND p.historical_model_run_id=h.id);

 IF v_workspace THEN
  FOREACH v_table IN ARRAY ARRAY['transfer_manifests','import_batches','profile_revision_evidence',
   'profile_revision_claims','claim_evidence_links','professional_claims','profiles','evidence_records',
   'posting_duplicate_decisions','saved_opportunities','posting_requirement_revisions','job_source_observations',
   'job_postings','encrypted_credentials','external_accounts','local_model_route_blocks',
   'event_metadata','event_receipts','outbox_events','audit_events'] LOOP
   EXECUTE format('DELETE FROM bonafides.%I WHERE owner_id=$1',v_table) USING c.owner_id;
  END LOOP;
  -- Preserve the installation-wide singleton while erasing its owner binding.
  UPDATE bonafides.local_model_reservations SET owner_id=NULL,workflow_run_id=NULL,
   fence=NULL,reserved_until='-infinity' WHERE singleton AND owner_id=c.owner_id;
  -- Retain only lifecycle rows referenced by immutable installation receipts.
  DELETE FROM bonafides.workflow_runs w WHERE w.owner_id=c.owner_id AND w.id<>c.id
   AND NOT EXISTS(SELECT 1 FROM bonafides.installation_controls k WHERE k.id=w.id);
  -- Reviews restrict config deletion; the reverse current-review FK is deferred.
  DELETE FROM bonafides.source_policy_reviews WHERE owner_id=c.owner_id;
  DELETE FROM bonafides.source_configs WHERE owner_id=c.owner_id;
  UPDATE bonafides.owner_settings SET version=version+1,workspace_version=workspace_version+1,
   privacy_mode='local_only',timezone='UTC',settings='{}',updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
  IF c.purpose='privacy_delete' THEN
   INSERT INTO bonafides.profiles(owner_id) VALUES(c.owner_id);
  END IF;
 ELSE
  UPDATE bonafides.owner_settings SET workspace_version=workspace_version+1,updated_at=clock_timestamp() WHERE owner_id=c.owner_id;
 END IF;

 WITH removed AS (
  DELETE FROM bonafides.blobs b WHERE b.owner_id=c.owner_id AND b.id=ANY(v_blobs)
   AND NOT EXISTS(SELECT 1 FROM bonafides.evidence_revisions r WHERE r.owner_id=c.owner_id AND r.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.application_attachments a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.document_artifacts a WHERE a.owner_id=c.owner_id AND a.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.import_batches i WHERE i.owner_id=c.owner_id AND i.blob_id=b.id)
   AND NOT EXISTS(SELECT 1 FROM bonafides.transfer_manifests t WHERE t.owner_id=c.owner_id AND t.blob_id=b.id)
  RETURNING storage_key,sha256,byte_length
 ) INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'cas',storage_key,sha256,byte_length FROM removed ON CONFLICT DO NOTHING;
 -- Portable exports contain the whole owner scope, even for application erasure.
 INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
 SELECT c.id,'export',workflow_run_id::text||'.zip',archive_sha256,byte_length
 FROM bonafides.portable_exports WHERE owner_id=c.owner_id AND state<>'expired'
 ON CONFLICT DO NOTHING;
 FOR v_item IN SELECT value FROM jsonb_array_elements(p_orphans) LOOP
  IF v_item->>'kind' IS NULL OR v_item->>'kind' NOT IN ('cas','cas_staging') THEN
   RAISE EXCEPTION 'invalid orphan scope' USING ERRCODE='55000';
  END IF;
  INSERT INTO bonafides.portable_purge_items(control_id,kind,storage_key,sha256,byte_length)
  SELECT c.id,v_item->>'kind',v_item->>'storage_key',v_item->>'sha256',(v_item->>'byte_length')::bigint
  WHERE v_item->>'kind'='cas_staging' OR NOT EXISTS(
   SELECT 1 FROM bonafides.blobs WHERE owner_id=c.owner_id AND storage_key=v_item->>'storage_key')
  ON CONFLICT DO NOTHING;
 END LOOP;
 DELETE FROM bonafides.oauth_transactions WHERE owner_id=c.owner_id;
 DELETE FROM bonafides.sessions WHERE owner_id=c.owner_id;
 UPDATE bonafides.owners SET auth_epoch=auth_epoch+1 WHERE id=c.owner_id;
 UPDATE bonafides.action_approvals SET revoked_at=clock_timestamp(),version=version+1 WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 UPDATE bonafides.hosted_egress_consents SET revoked_at=clock_timestamp() WHERE owner_id=c.owner_id AND revoked_at IS NULL;
 SELECT count(*) INTO v_count FROM bonafides.portable_purge_items WHERE control_id=c.id;
 INSERT INTO bonafides.audit_events(owner_id,action,actor_kind,actor_id,request_id,entity_type,entity_id,detail)
 VALUES(c.owner_id,'data_erasure_committed','worker',c.holder::text,c.id,'installation_control',c.id,
  jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,'purge_items',v_count));
 PERFORM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 RETURN jsonb_build_object('scope',CASE WHEN v_workspace THEN 'workspace' ELSE 'application' END,
  'application_id',v_application,'purge_items',v_count,'filesystem_complete',false);
END $_$;


ALTER FUNCTION bonafides.erase_portable_scope(p_id uuid, p_holder uuid, p_fence bigint, p_checkpoint uuid, p_runtime_pins jsonb, p_orphans jsonb) OWNER TO bonafides_migrator;

-- Name: erased_request_keys erased_request_keys_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.erased_request_keys
    ADD CONSTRAINT erased_request_keys_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: erased_request_keys erased_request_keys_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.erased_request_keys
    ADD CONSTRAINT erased_request_keys_pkey PRIMARY KEY (owner_id, route_sha256, key_sha256);

-- Name: erased_request_keys; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.erased_request_keys (
    owner_id uuid NOT NULL,
    route_sha256 text NOT NULL,
    key_sha256 text NOT NULL,
    request_sha256 text NOT NULL,
    erased_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT erased_request_keys_key_sha256_check CHECK ((key_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT erased_request_keys_request_sha256_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT erased_request_keys_route_sha256_check CHECK ((route_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.erased_request_keys OWNER TO bonafides_migrator;

-- Name: event_metadata event_metadata_owner_id_event_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.event_metadata
    ADD CONSTRAINT event_metadata_owner_id_event_id_fkey FOREIGN KEY (owner_id, event_id) REFERENCES bonafides.outbox_events(owner_id, id);

-- Name: event_metadata event_metadata_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.event_metadata
    ADD CONSTRAINT event_metadata_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: event_metadata event_metadata_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.event_metadata
    ADD CONSTRAINT event_metadata_pkey PRIMARY KEY (owner_id, event_id);

-- Name: event_metadata; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.event_metadata (
    owner_id uuid NOT NULL,
    event_id uuid NOT NULL,
    event_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE bonafides.event_metadata OWNER TO bonafides_migrator;

-- Name: event_receipts event_receipts_owner_id_event_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.event_receipts
    ADD CONSTRAINT event_receipts_owner_id_event_id_fkey FOREIGN KEY (owner_id, event_id) REFERENCES bonafides.outbox_events(owner_id, id) ON DELETE CASCADE;

-- Name: event_receipts event_receipts_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.event_receipts
    ADD CONSTRAINT event_receipts_pkey PRIMARY KEY (owner_id, consumer_key, event_id);

-- Name: event_receipts; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.event_receipts (
    owner_id uuid NOT NULL,
    consumer_key text NOT NULL,
    event_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE bonafides.event_receipts OWNER TO bonafides_migrator;

-- Name: evidence_blob_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX evidence_blob_idx ON bonafides.evidence_revisions USING btree (owner_id, blob_id);

-- Name: evidence_classification_changes evidence_classification_changes_owner_id_evidence_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_classification_changes
    ADD CONSTRAINT evidence_classification_changes_owner_id_evidence_id_fkey FOREIGN KEY (owner_id, evidence_id) REFERENCES bonafides.evidence_records(owner_id, id) ON DELETE CASCADE;

-- Name: evidence_classification_changes evidence_classification_changes_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_classification_changes
    ADD CONSTRAINT evidence_classification_changes_pkey PRIMARY KEY (owner_id, evidence_id, sequence);

-- Name: evidence_classification_changes immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.evidence_classification_changes FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: evidence_classification_changes; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.evidence_classification_changes (
    owner_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    sequence integer NOT NULL,
    privacy_class text NOT NULL,
    employer_owned boolean NOT NULL,
    changed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT evidence_classification_changes_privacy_class_check CHECK ((privacy_class = ANY (ARRAY['public'::text, 'private'::text, 'confidential'::text]))),
    CONSTRAINT evidence_classification_changes_sequence_check CHECK ((sequence > 0))
);


ALTER TABLE bonafides.evidence_classification_changes OWNER TO bonafides_migrator;

-- Name: evidence_records evidence_records_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_records
    ADD CONSTRAINT evidence_records_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: evidence_records evidence_records_owner_id_id_current_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_records
    ADD CONSTRAINT evidence_records_owner_id_id_current_revision_id_fkey FOREIGN KEY (owner_id, id, current_revision_id) REFERENCES bonafides.evidence_revisions(owner_id, evidence_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: evidence_records evidence_records_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_records
    ADD CONSTRAINT evidence_records_pkey PRIMARY KEY (owner_id, id);

-- Name: evidence_records; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.evidence_records (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    current_revision_id uuid,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    privacy_class text NOT NULL,
    employer_owned boolean NOT NULL,
    CONSTRAINT evidence_records_privacy_class_check CHECK ((privacy_class = ANY (ARRAY['public'::text, 'private'::text, 'confidential'::text]))),
    CONSTRAINT evidence_records_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.evidence_records OWNER TO bonafides_migrator;

-- Name: evidence_revisions evidence_revisions_owner_id_blob_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_revisions
    ADD CONSTRAINT evidence_revisions_owner_id_blob_id_fkey FOREIGN KEY (owner_id, blob_id) REFERENCES bonafides.blobs(owner_id, id) ON DELETE RESTRICT;

-- Name: evidence_revisions evidence_revisions_owner_id_evidence_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_revisions
    ADD CONSTRAINT evidence_revisions_owner_id_evidence_id_fkey FOREIGN KEY (owner_id, evidence_id) REFERENCES bonafides.evidence_records(owner_id, id) ON DELETE CASCADE;

-- Name: evidence_revisions evidence_revisions_owner_id_evidence_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_revisions
    ADD CONSTRAINT evidence_revisions_owner_id_evidence_id_id_key UNIQUE (owner_id, evidence_id, id);

-- Name: evidence_revisions evidence_revisions_owner_id_evidence_id_revision_no_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_revisions
    ADD CONSTRAINT evidence_revisions_owner_id_evidence_id_revision_no_key UNIQUE (owner_id, evidence_id, revision_no);

-- Name: evidence_revisions evidence_revisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.evidence_revisions
    ADD CONSTRAINT evidence_revisions_pkey PRIMARY KEY (owner_id, id);

-- Name: evidence_revisions immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.evidence_revisions FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: evidence_revisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.evidence_revisions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evidence_id uuid NOT NULL,
    revision_no bigint NOT NULL,
    payload jsonb NOT NULL,
    blob_id uuid,
    content_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_type text NOT NULL,
    origin text NOT NULL,
    CONSTRAINT evidence_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT evidence_revisions_origin_check CHECK ((origin = ANY (ARRAY['owner_entry'::text, 'resume_import'::text]))),
    CONSTRAINT evidence_revisions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT evidence_revisions_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT evidence_revisions_source_type_check CHECK ((source_type = ANY (ARRAY['owner_statement'::text, 'document'::text, 'work_sample'::text, 'web_page'::text, 'credential'::text, 'other'::text])))
);


ALTER TABLE bonafides.evidence_revisions OWNER TO bonafides_migrator;

-- Name: external_accounts external_accounts_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.external_accounts
    ADD CONSTRAINT external_accounts_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: external_accounts external_accounts_owner_id_provider_key_external_subject_ha_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.external_accounts
    ADD CONSTRAINT external_accounts_owner_id_provider_key_external_subject_ha_key UNIQUE (owner_id, provider_key, external_subject_hash);

-- Name: external_accounts external_accounts_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.external_accounts
    ADD CONSTRAINT external_accounts_pkey PRIMARY KEY (owner_id, id);

-- Name: external_accounts; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.external_accounts (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_key text NOT NULL,
    external_subject_hash text NOT NULL,
    granted_scopes text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'disconnected'::text NOT NULL,
    capability_flags jsonb DEFAULT '{}'::jsonb NOT NULL,
    sync_cursor_ciphertext bytea,
    sync_cursor_key_id text,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT external_accounts_status_check CHECK ((status = ANY (ARRAY['connected'::text, 'revocation_pending'::text, 'disconnected'::text, 'reauthorize'::text]))),
    CONSTRAINT external_accounts_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.external_accounts OWNER TO bonafides_migrator;

-- Name: fail_portable_export_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.fail_portable_export_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' OR c.purpose<>'portable_export'
 OR c.result IS DISTINCT FROM '{"error_code":"portable_export_failed"}'::jsonb
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.portable_exports e
  WHERE e.owner_id=c.owner_id AND e.workflow_run_id=c.id AND e.state<>'expired') THEN
  RAISE EXCEPTION 'export failure settlement refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
  version=version+1,updated_at=clock_timestamp()
 WHERE owner_id=c.owner_id AND id<>c.id AND state IN ('pending','retryable_failure');
 UPDATE bonafides.workflow_runs SET state='retryable_failure',maintenance_epoch=c.maintenance_epoch,
  lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,
  next_attempt_at=clock_timestamp(),updated_at=clock_timestamp(),last_error_code='worker_interrupted'
 WHERE owner_id=c.owner_id AND id<>c.id AND state='running';
 UPDATE bonafides.workflow_runs SET state='permanent_failure',result=c.result,
  finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),last_error_code='portable_export_failed'
 WHERE owner_id=c.owner_id AND id=c.id;
 INSERT INTO bonafides.workflow_attempt_events(owner_id,workflow_run_id,attempt,fence,event,error_code)
 SELECT owner_id,id,attempt,fence,'permanent_failure','portable_export_failed'
 FROM bonafides.workflow_runs WHERE owner_id=c.owner_id AND id=c.id;
 UPDATE bonafides.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton;
END $$;


ALTER FUNCTION bonafides.fail_portable_export_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: finalize_portable_preview(uuid, uuid, jsonb, text, timestamp with time zone); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.finalize_portable_preview(p_owner uuid, p_id uuid, p_plan jsonb, p_sha text, p_expiry timestamp with time zone) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $_$
DECLARE p bonafides.portable_previews%ROWTYPE; ready_at timestamptz;
 candidate_length bigint; chunk_count bigint; expected bigint;
BEGIN
 SELECT * INTO STRICT p FROM bonafides.portable_previews
 WHERE owner_id=p_owner AND id=p_id FOR UPDATE;
 ready_at=clock_timestamp();
 expected=(p.plan->>'byte_length')::bigint;
 SELECT COALESCE(sum(octet_length(payload)),0)+octet_length(p.candidate_bytes),count(*)
 INTO candidate_length,chunk_count FROM bonafides.portable_preview_chunks
 WHERE owner_id=p_owner AND preview_id=p_id;
 IF p.action_kind<>'replace_workspace' OR p.cleared_at IS NOT NULL
 OR p.candidate_bytes IS NULL OR p.retained_at IS NOT NULL
 OR (p_plan-'expires_at') IS DISTINCT FROM (p.plan-'expires_at')
 OR p_expiry IS NULL OR (p_plan->>'expires_at')::timestamptz IS DISTINCT FROM p_expiry
 OR p_expiry<=ready_at OR p_expiry>ready_at+interval '10 minutes'
 OR p_sha IS NULL OR p_sha!~'^[0-9a-f]{64}$'
 OR candidate_length IS DISTINCT FROM expected OR chunk_count<>(expected-1)/1048576
 OR NOT EXISTS(SELECT 1 FROM bonafides.workflow_runs
  WHERE owner_id=p_owner AND id=p.workflow_run_id AND state='pending')
 OR EXISTS(SELECT 1 FROM bonafides.action_approvals
  WHERE owner_id=p_owner AND workflow_run_id=p.workflow_run_id) THEN
  RAISE EXCEPTION 'portable preview finalization refused' USING ERRCODE='23514';
 END IF;
 UPDATE bonafides.portable_previews SET plan=p_plan,preview_sha256=p_sha,
  created_at=ready_at,expires_at=p_expiry,retained_at=ready_at
 WHERE owner_id=p_owner AND id=p_id;
END $_$;


ALTER FUNCTION bonafides.finalize_portable_preview(p_owner uuid, p_id uuid, p_plan jsonb, p_sha text, p_expiry timestamp with time zone) OWNER TO bonafides_migrator;

-- Name: guard_import_batch(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.guard_import_batch() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.blob_id,NEW.media_type,NEW.byte_size,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.blob_id,OLD.media_type,OLD.byte_size,OLD.created_at)
 OR (OLD.parsed_text IS NOT NULL AND ROW(NEW.parsed_text,NEW.source_sha256,NEW.proposals,NEW.warnings,NEW.review_expires_at)
 IS DISTINCT FROM ROW(OLD.parsed_text,OLD.source_sha256,OLD.proposals,OLD.warnings,OLD.review_expires_at))
 OR (OLD.review IS NOT NULL AND ROW(NEW.review,NEW.reviewed_at,NEW.profile_version)
 IS DISTINCT FROM ROW(OLD.review,OLD.reviewed_at,OLD.profile_version))
 OR (OLD.merged_profile_revision_id IS NOT NULL AND NEW.merged_profile_revision_id IS DISTINCT FROM OLD.merged_profile_revision_id)
 THEN RAISE EXCEPTION 'immutable import phase' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard_import_batch() OWNER TO bonafides_migrator;

-- Name: guard_portable_preview_chunk(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.guard_portable_preview_chunk() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE p bonafides.portable_previews%ROWTYPE; next_chunk integer; expected bigint;
BEGIN
 SELECT * INTO STRICT p FROM bonafides.portable_previews
 WHERE owner_id=NEW.owner_id AND id=NEW.preview_id FOR UPDATE;
 expected=(p.plan->>'byte_length')::bigint;
 SELECT COALESCE(max(chunk_no),0)+1 INTO next_chunk FROM bonafides.portable_preview_chunks
 WHERE owner_id=NEW.owner_id AND preview_id=NEW.preview_id;
 IF p.cleared_at IS NOT NULL OR p.candidate_bytes IS NULL
 OR octet_length(p.candidate_bytes)<>1048576 OR expected NOT BETWEEN 1 AND 671109611520
 OR NEW.chunk_no<>next_chunk
 OR NEW.chunk_no::bigint*1048576+octet_length(NEW.payload)>expected
 OR octet_length(NEW.payload)<>LEAST(1048576,expected-NEW.chunk_no::bigint*1048576) THEN
  RAISE EXCEPTION 'portable candidate chunk refused' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard_portable_preview_chunk() OWNER TO bonafides_migrator;

-- Name: guard_posting_source(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.guard_posting_source() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.source_config_id,NEW.external_id,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.source_config_id,OLD.external_id,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable posting attribution' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard_posting_source() OWNER TO bonafides_migrator;

-- Name: guard_source_identity(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.guard_source_identity() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.source_kind,NEW.board_token,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.owner_id,OLD.id,OLD.source_kind,OLD.board_token,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable source identity' USING ERRCODE='55000'; END IF;
 IF OLD.removed_at IS NOT NULL AND ROW(NEW.company,NEW.enabled,NEW.terms_url,NEW.terms_revision,
 NEW.terms_reviewed_at,NEW.allowed_host,NEW.request_limit_per_minute,NEW.current_policy_review_id,NEW.policy_version,NEW.removed_at)
 IS DISTINCT FROM ROW(OLD.company,OLD.enabled,OLD.terms_url,OLD.terms_revision,
 OLD.terms_reviewed_at,OLD.allowed_host,OLD.request_limit_per_minute,OLD.current_policy_review_id,OLD.policy_version,OLD.removed_at)
 THEN RAISE EXCEPTION 'retired source is immutable' USING ERRCODE='55000'; END IF;
 IF OLD.removed_at IS NULL AND NEW.removed_at IS NOT NULL THEN
  IF NEW.source_kind<>'greenhouse' OR NEW.enabled OR NEW.version<=OLD.version OR
   ROW(NEW.company,NEW.terms_url,NEW.terms_revision,NEW.terms_reviewed_at,NEW.allowed_host,
   NEW.request_limit_per_minute,NEW.current_policy_review_id,NEW.policy_version)
   IS DISTINCT FROM ROW(OLD.company,OLD.terms_url,OLD.terms_revision,OLD.terms_reviewed_at,OLD.allowed_host,
   OLD.request_limit_per_minute,OLD.current_policy_review_id,OLD.policy_version)
  THEN RAISE EXCEPTION 'invalid source retirement' USING ERRCODE='55000'; END IF;
 ELSIF ROW(NEW.company,NEW.enabled,NEW.terms_url,NEW.terms_revision,NEW.terms_reviewed_at,NEW.allowed_host,
 NEW.request_limit_per_minute,NEW.current_policy_review_id)
 IS DISTINCT FROM ROW(OLD.company,OLD.enabled,OLD.terms_url,OLD.terms_revision,OLD.terms_reviewed_at,OLD.allowed_host,
 OLD.request_limit_per_minute,OLD.current_policy_review_id)
 AND (NEW.policy_version<=OLD.policy_version OR NEW.version<=OLD.version)
 THEN RAISE EXCEPTION 'source policy version required' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard_source_identity() OWNER TO bonafides_migrator;

-- Name: guard_source_workflow_pin(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.guard_source_workflow_pin() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF OLD.kind='source_refresh' AND ROW(NEW.kind,NEW.input,NEW.source_config_id,NEW.source_policy_version,
 NEW.source_policy_review_id,NEW.source_policy_sha256,NEW.max_attempts,NEW.deadline_at,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.kind,OLD.input,OLD.source_config_id,OLD.source_policy_version,
 OLD.source_policy_review_id,OLD.source_policy_sha256,OLD.max_attempts,OLD.deadline_at,OLD.created_at)
 THEN RAISE EXCEPTION 'immutable source workflow policy' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.guard_source_workflow_pin() OWNER TO bonafides_migrator;

-- Name: heartbeat_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.heartbeat_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 UPDATE bonafides.installation_controls SET
  lease_expires_at=least(deadline_at,clock_timestamp()+interval '30 seconds') WHERE id=c.id;
 UPDATE bonafides.workflow_runs SET
  lease_expires_at=least(c.deadline_at,clock_timestamp()+interval '30 seconds'),
  last_heartbeat_at=clock_timestamp() WHERE owner_id=c.owner_id AND id=c.id;
END $$;


ALTER FUNCTION bonafides.heartbeat_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: hosted_egress_consents hosted_consent_approval_binding; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.hosted_egress_consents
    ADD CONSTRAINT hosted_consent_approval_binding FOREIGN KEY (owner_id, approval_id, workflow_run_id) REFERENCES bonafides.action_approvals(owner_id, id, workflow_run_id) DEFERRABLE;

-- Name: hosted_egress_consents hosted_egress_consents_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.hosted_egress_consents
    ADD CONSTRAINT hosted_egress_consents_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: hosted_egress_consents hosted_egress_consents_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.hosted_egress_consents
    ADD CONSTRAINT hosted_egress_consents_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: hosted_egress_consents hosted_egress_consents_owner_id_workflow_run_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.hosted_egress_consents
    ADD CONSTRAINT hosted_egress_consents_owner_id_workflow_run_id_key UNIQUE (owner_id, workflow_run_id);

-- Name: hosted_egress_consents hosted_egress_consents_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.hosted_egress_consents
    ADD CONSTRAINT hosted_egress_consents_pkey PRIMARY KEY (owner_id, id);

-- Name: hosted_egress_consents immutable_hosted_binding; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_hosted_binding BEFORE UPDATE ON bonafides.hosted_egress_consents FOR EACH ROW EXECUTE FUNCTION bonafides.protect_hosted_consent_binding();

-- Name: hosted_egress_consents; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.hosted_egress_consents (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    provider_key text NOT NULL,
    model_id text NOT NULL,
    purpose text NOT NULL,
    input_scope_sha256 text NOT NULL,
    settings_version bigint NOT NULL,
    acknowledged_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    approval_id uuid,
    transmission_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT hosted_egress_consents_check CHECK ((expires_at > acknowledged_at)),
    CONSTRAINT hosted_egress_consents_input_scope_sha256_check CHECK ((input_scope_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT hosted_egress_consents_purpose_check CHECK ((purpose = ANY (ARRAY['profile_import'::text, 'posting_extract'::text, 'matching'::text, 'document_generate'::text]))),
    CONSTRAINT hosted_egress_consents_settings_version_check CHECK ((settings_version > 0)),
    CONSTRAINT hosted_egress_consents_transmission_count_check CHECK (((transmission_count >= 0) AND (transmission_count <= 2)))
);


ALTER TABLE bonafides.hosted_egress_consents OWNER TO bonafides_migrator;

-- Name: idempotency_expiry_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX idempotency_expiry_idx ON bonafides.idempotency_records USING btree (expires_at);

-- Name: idempotency_records idempotency_records_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.idempotency_records
    ADD CONSTRAINT idempotency_records_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: idempotency_records idempotency_records_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.idempotency_records
    ADD CONSTRAINT idempotency_records_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: idempotency_records idempotency_records_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.idempotency_records
    ADD CONSTRAINT idempotency_records_pkey PRIMARY KEY (owner_id, route_key, key);

-- Name: idempotency_records; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.idempotency_records (
    owner_id uuid NOT NULL,
    route_key text NOT NULL,
    key text NOT NULL,
    request_sha256 text NOT NULL,
    response_status integer,
    response_body jsonb,
    workflow_run_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT idempotency_records_check CHECK ((expires_at > created_at)),
    CONSTRAINT idempotency_records_key_check CHECK (((length(key) >= 16) AND (length(key) <= 200))),
    CONSTRAINT idempotency_records_request_sha256_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT idempotency_records_response_status_check CHECK (((response_status >= 200) AND (response_status <= 599)))
);


ALTER TABLE bonafides.idempotency_records OWNER TO bonafides_migrator;

-- Name: import_batches import_batches_owner_id_blob_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_owner_id_blob_id_fkey FOREIGN KEY (owner_id, blob_id) REFERENCES bonafides.blobs(owner_id, id) ON DELETE RESTRICT;

-- Name: import_batches import_batches_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: import_batches import_batches_owner_id_merged_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_owner_id_merged_profile_revision_id_fkey FOREIGN KEY (owner_id, merged_profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: import_batches import_batches_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: import_batches import_batches_owner_id_workflow_run_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_owner_id_workflow_run_id_key UNIQUE (owner_id, workflow_run_id);

-- Name: import_batches import_batches_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.import_batches
    ADD CONSTRAINT import_batches_pkey PRIMARY KEY (owner_id, id);

-- Name: import_batches import_phase_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER import_phase_guard BEFORE UPDATE ON bonafides.import_batches FOR EACH ROW EXECUTE FUNCTION bonafides.guard_import_batch();

-- Name: import_batches; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.import_batches (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    blob_id uuid NOT NULL,
    media_type text NOT NULL,
    byte_size integer NOT NULL,
    profile_version bigint NOT NULL,
    source_sha256 text,
    parsed_text text,
    proposals jsonb DEFAULT '[]'::jsonb NOT NULL,
    warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    review jsonb,
    review_expires_at timestamp with time zone,
    reviewed_at timestamp with time zone,
    merged_profile_revision_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT import_batches_byte_size_check CHECK (((byte_size >= 1) AND (byte_size <= 10485760))),
    CONSTRAINT import_batches_check CHECK (((parsed_text IS NULL) = (source_sha256 IS NULL))),
    CONSTRAINT import_batches_check1 CHECK (((parsed_text IS NULL) = (review_expires_at IS NULL))),
    CONSTRAINT import_batches_check2 CHECK (((review IS NULL) = (reviewed_at IS NULL))),
    CONSTRAINT import_batches_media_type_check CHECK ((media_type = ANY (ARRAY['application/pdf'::text, 'text/plain'::text]))),
    CONSTRAINT import_batches_parsed_text_check CHECK (((length(parsed_text) <= 80000) AND (octet_length(parsed_text) <= 262144))),
    CONSTRAINT import_batches_profile_version_check CHECK ((profile_version > 0)),
    CONSTRAINT import_batches_proposals_check CHECK (((jsonb_typeof(proposals) = 'array'::text) AND (jsonb_array_length(proposals) <= 100))),
    CONSTRAINT import_batches_review_check CHECK ((jsonb_typeof(review) = 'object'::text)),
    CONSTRAINT import_batches_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT import_batches_warnings_check CHECK ((jsonb_typeof(warnings) = 'array'::text))
);


ALTER TABLE bonafides.import_batches OWNER TO bonafides_migrator;

-- Name: import_owner_list_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX import_owner_list_idx ON bonafides.import_batches USING btree (owner_id, created_at DESC, id);

-- Name: import_review_expiry_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX import_review_expiry_idx ON bonafides.import_batches USING btree (review_expires_at) WHERE (review IS NULL);

-- Name: inspect_maintenance_recovery(uuid, bigint, uuid, uuid, bigint, uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.inspect_maintenance_recovery(p_namespace uuid, p_epoch bigint, p_control uuid, p_holder uuid, p_fence bigint, p_guard uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE i bonafides.installation_state%ROWTYPE; c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM bonafides.installation_state WHERE singleton FOR UPDATE;
 SELECT * INTO c FROM bonafides.installation_controls WHERE phase<>'released' FOR UPDATE;
 IF i.namespace_id IS DISTINCT FROM p_namespace OR i.maintenance_epoch IS DISTINCT FROM p_epoch
 OR i.execution_guard_id IS DISTINCT FROM p_guard OR p_guard IS NULL
 OR c.id IS DISTINCT FROM p_control
 OR (c.id IS NOT NULL AND (NOT i.maintenance_active OR c.holder IS DISTINCT FROM p_holder
  OR c.fence IS DISTINCT FROM p_fence OR c.namespace_id<>i.namespace_id
  OR c.maintenance_epoch<>i.maintenance_epoch OR c.execution_guard_id IS DISTINCT FROM p_guard))
 OR (c.id IS NULL AND (p_holder IS NOT NULL OR p_fence IS NOT NULL
  OR (i.maintenance_active AND NOT EXISTS(
   SELECT 1 FROM bonafides.maintenance_recoveries r WHERE r.control_id IS NULL
   AND r.namespace_id=p_namespace AND r.maintenance_epoch=p_epoch
   AND r.execution_guard_id=p_guard AND r.outcome IS NULL)))) THEN
  RAISE EXCEPTION 'offline recovery identity or legacy control refused' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM bonafides.provider_executions) THEN
  RAISE EXCEPTION 'provider execution remains unknown' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM bonafides.installation_activities WHERE
  execution_guard_id IS DISTINCT FROM p_guard OR namespace_id<>p_namespace
  OR maintenance_epoch>p_epoch) THEN
  RAISE EXCEPTION 'legacy or foreign activity cannot be proven stopped' USING ERRCODE='55000';
 END IF;
END $$;


ALTER FUNCTION bonafides.inspect_maintenance_recovery(p_namespace uuid, p_epoch bigint, p_control uuid, p_holder uuid, p_fence bigint, p_guard uuid) OWNER TO bonafides_migrator;

-- Name: installation_activities installation_activities_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_activities
    ADD CONSTRAINT installation_activities_pkey PRIMARY KEY (id);

-- Name: installation_activities; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.installation_activities (
    id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    participant_id uuid NOT NULL,
    kind text NOT NULL,
    execution_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    execution_guard_id uuid,
    CONSTRAINT installation_activities_kind_check CHECK ((kind = ANY (ARRAY['api'::text, 'worker'::text]))),
    CONSTRAINT installation_activities_maintenance_epoch_check CHECK ((maintenance_epoch >= 0))
);


ALTER TABLE bonafides.installation_activities OWNER TO bonafides_migrator;

-- Name: installation_activities_namespace_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX installation_activities_namespace_idx ON bonafides.installation_activities USING btree (namespace_id, maintenance_epoch);

-- Name: installation_backups installation_backups_file_name_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_backups
    ADD CONSTRAINT installation_backups_file_name_key UNIQUE (file_name);

-- Name: installation_backups installation_backups_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_backups
    ADD CONSTRAINT installation_backups_id_fkey FOREIGN KEY (id) REFERENCES bonafides.installation_controls(id);

-- Name: installation_backups installation_backups_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_backups
    ADD CONSTRAINT installation_backups_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id);

-- Name: installation_backups installation_backups_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_backups
    ADD CONSTRAINT installation_backups_pkey PRIMARY KEY (id);

-- Name: installation_backups; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.installation_backups (
    id uuid NOT NULL,
    owner_id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    workspace_version bigint NOT NULL,
    installation_auth_epoch bigint NOT NULL,
    owner_auth_epoch bigint NOT NULL,
    schema_versions jsonb NOT NULL,
    image_sha256 text NOT NULL,
    source_sha256 text NOT NULL,
    config_sha256 text NOT NULL,
    keyring_sha256 text NOT NULL,
    recipient_sha256 text NOT NULL,
    file_name text NOT NULL,
    cipher_sha256 text NOT NULL,
    manifest_sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    registered_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    restore_verified_at timestamp with time zone,
    restore_receipt_sha256 text,
    restore_target_namespace uuid,
    cipher_verified_at timestamp with time zone,
    copy_verified_at timestamp with time zone,
    pruned_at timestamp with time zone,
    copy_pruned_at timestamp with time zone,
    CONSTRAINT installation_backups_byte_length_check CHECK (((byte_length >= 1) AND (byte_length <= '4294967296'::bigint))),
    CONSTRAINT installation_backups_check CHECK ((((restore_verified_at IS NULL) AND (restore_receipt_sha256 IS NULL) AND (restore_target_namespace IS NULL)) OR ((restore_verified_at IS NOT NULL) AND (restore_receipt_sha256 IS NOT NULL) AND (restore_target_namespace IS NOT NULL) AND (restore_target_namespace <> namespace_id)))),
    CONSTRAINT installation_backups_cipher_sha256_check CHECK ((cipher_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_config_sha256_check CHECK ((config_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_file_name_check CHECK ((file_name ~ '^[0-9a-f-]{36}\.cms$'::text)),
    CONSTRAINT installation_backups_image_sha256_check CHECK ((image_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_installation_auth_epoch_check CHECK ((installation_auth_epoch > 0)),
    CONSTRAINT installation_backups_keyring_sha256_check CHECK ((keyring_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_maintenance_epoch_check CHECK ((maintenance_epoch > 0)),
    CONSTRAINT installation_backups_manifest_sha256_check CHECK ((manifest_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_owner_auth_epoch_check CHECK ((owner_auth_epoch > 0)),
    CONSTRAINT installation_backups_recipient_sha256_check CHECK ((recipient_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_restore_receipt_sha256_check CHECK ((restore_receipt_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_schema_versions_check CHECK ((jsonb_typeof(schema_versions) = 'object'::text)),
    CONSTRAINT installation_backups_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_backups_workspace_version_check CHECK ((workspace_version > 0))
);


ALTER TABLE bonafides.installation_backups OWNER TO bonafides_migrator;

-- Name: installation_controls installation_controls_owner_id_approval_id_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_controls
    ADD CONSTRAINT installation_controls_owner_id_approval_id_id_fkey FOREIGN KEY (owner_id, approval_id, id) REFERENCES bonafides.action_approvals(owner_id, id, workflow_run_id);

-- Name: installation_controls installation_controls_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_controls
    ADD CONSTRAINT installation_controls_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id);

-- Name: installation_controls installation_controls_owner_id_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_controls
    ADD CONSTRAINT installation_controls_owner_id_id_fkey FOREIGN KEY (owner_id, id) REFERENCES bonafides.workflow_runs(owner_id, id);

-- Name: installation_controls installation_controls_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_controls
    ADD CONSTRAINT installation_controls_pkey PRIMARY KEY (id);

-- Name: installation_controls; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.installation_controls (
    id uuid NOT NULL,
    owner_id uuid NOT NULL,
    holder uuid NOT NULL,
    fence bigint NOT NULL,
    workflow_fence bigint NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    purpose text NOT NULL,
    approval_id uuid,
    scope jsonb NOT NULL,
    phase text DEFAULT 'draining'::text NOT NULL,
    deadline_at timestamp with time zone NOT NULL,
    drain_deadline_at timestamp with time zone NOT NULL,
    lease_expires_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    settled_at timestamp with time zone,
    released_at timestamp with time zone,
    result jsonb,
    execution_guard_id uuid,
    CONSTRAINT installation_controls_check CHECK ((drain_deadline_at <= deadline_at)),
    CONSTRAINT installation_controls_check1 CHECK (((purpose = ANY (ARRAY['portable_import'::text, 'privacy_delete'::text, 'restore_validate'::text])) = (approval_id IS NOT NULL))),
    CONSTRAINT installation_controls_check2 CHECK (((phase = ANY (ARRAY['settled'::text, 'released'::text])) = (settled_at IS NOT NULL))),
    CONSTRAINT installation_controls_check3 CHECK (((phase = 'released'::text) = (released_at IS NOT NULL))),
    CONSTRAINT installation_controls_fence_check CHECK ((fence > 0)),
    CONSTRAINT installation_controls_maintenance_epoch_check CHECK ((maintenance_epoch > 0)),
    CONSTRAINT installation_controls_phase_check CHECK ((phase = ANY (ARRAY['draining'::text, 'quiescent'::text, 'settled'::text, 'released'::text]))),
    CONSTRAINT installation_controls_purpose_check CHECK ((purpose = ANY (ARRAY['portable_export'::text, 'portable_import'::text, 'privacy_delete'::text, 'backup'::text, 'restore_validate'::text]))),
    CONSTRAINT installation_controls_result_check CHECK (((result IS NULL) OR (jsonb_typeof(result) = 'object'::text))),
    CONSTRAINT installation_controls_scope_check CHECK ((jsonb_typeof(scope) = 'object'::text)),
    CONSTRAINT installation_controls_workflow_fence_check CHECK ((workflow_fence > 0))
);


ALTER TABLE bonafides.installation_controls OWNER TO bonafides_migrator;

-- Name: installation_one_active_control_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX installation_one_active_control_idx ON bonafides.installation_controls USING btree ((true)) WHERE (phase <> 'released'::text);

-- Name: installation_state installation_state_namespace_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_state
    ADD CONSTRAINT installation_state_namespace_id_key UNIQUE (namespace_id);

-- Name: installation_state installation_state_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.installation_state
    ADD CONSTRAINT installation_state_pkey PRIMARY KEY (singleton);

-- Name: installation_state; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.installation_state (
    singleton boolean DEFAULT true NOT NULL,
    namespace_id uuid DEFAULT gen_random_uuid() NOT NULL,
    maintenance_active boolean DEFAULT false NOT NULL,
    maintenance_epoch bigint DEFAULT 0 NOT NULL,
    bootstrap_token_sha256 text,
    bootstrap_consumed_at timestamp with time zone,
    bootstrap_expires_at timestamp with time zone,
    auth_epoch bigint DEFAULT 1 NOT NULL,
    execution_guard_id uuid,
    CONSTRAINT installation_state_auth_epoch_check CHECK ((auth_epoch > 0)),
    CONSTRAINT installation_state_bootstrap_token_sha256_check CHECK ((bootstrap_token_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT installation_state_maintenance_epoch_check CHECK ((maintenance_epoch >= 0)),
    CONSTRAINT installation_state_singleton_check CHECK (singleton)
);


ALTER TABLE bonafides.installation_state OWNER TO bonafides_migrator;

-- Name: invalidate_posting_requirement_projection(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.invalidate_posting_requirement_projection() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.title,NEW.company,NEW.location_text,NEW.description_text,NEW.canonical_url,NEW.content_sha256)
 IS DISTINCT FROM ROW(OLD.title,OLD.company,OLD.location_text,OLD.description_text,OLD.canonical_url,OLD.content_sha256)
 THEN NEW.requirements='[]'::jsonb; NEW.requirements_reviewed_at=NULL; END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.invalidate_posting_requirement_projection() OWNER TO bonafides_migrator;

-- Name: job_postings job_postings_owner_id_dedupe_key_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_postings
    ADD CONSTRAINT job_postings_owner_id_dedupe_key_key UNIQUE (owner_id, dedupe_key);

-- Name: job_postings job_postings_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_postings
    ADD CONSTRAINT job_postings_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: job_postings job_postings_owner_id_source_config_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_postings
    ADD CONSTRAINT job_postings_owner_id_source_config_id_fkey FOREIGN KEY (owner_id, source_config_id) REFERENCES bonafides.source_configs(owner_id, id) ON DELETE RESTRICT;

-- Name: job_postings job_postings_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_postings
    ADD CONSTRAINT job_postings_pkey PRIMARY KEY (owner_id, id);

-- Name: job_postings posting_requirement_source_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER posting_requirement_source_guard BEFORE UPDATE ON bonafides.job_postings FOR EACH ROW EXECUTE FUNCTION bonafides.invalidate_posting_requirement_projection();

-- Name: job_postings posting_source_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER posting_source_guard BEFORE UPDATE ON bonafides.job_postings FOR EACH ROW EXECUTE FUNCTION bonafides.guard_posting_source();

-- Name: job_postings; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.job_postings (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    company text NOT NULL,
    location_text text DEFAULT ''::text NOT NULL,
    description_text text NOT NULL,
    canonical_url text,
    dedupe_key text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    requirements jsonb DEFAULT '[]'::jsonb NOT NULL,
    requirements_reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, ((((((COALESCE(title, ''::text) || ' '::text) || COALESCE(company, ''::text)) || ' '::text) || COALESCE(location_text, ''::text)) || ' '::text) || COALESCE(description_text, ''::text)))) STORED,
    source_config_id uuid,
    external_id text,
    content_sha256 text,
    last_observed_at timestamp with time zone,
    normalized_company text GENERATED ALWAYS AS (lower(btrim(regexp_replace(company, '[[:space:]]+'::text, ' '::text, 'g'::text)))) STORED,
    normalized_title text GENERATED ALWAYS AS (lower(btrim(regexp_replace(title, '[[:space:]]+'::text, ' '::text, 'g'::text)))) STORED,
    normalized_location text GENERATED ALWAYS AS (lower(btrim(regexp_replace(location_text, '[[:space:]]+'::text, ' '::text, 'g'::text)))) STORED,
    CONSTRAINT job_postings_company_check CHECK (((length(company) >= 1) AND (length(company) <= 500))),
    CONSTRAINT job_postings_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT job_postings_description_text_check CHECK ((length(description_text) <= 200000)),
    CONSTRAINT job_postings_requirements_check CHECK ((jsonb_typeof(requirements) = 'array'::text)),
    CONSTRAINT job_postings_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text, 'unknown'::text]))),
    CONSTRAINT job_postings_title_check CHECK (((length(title) >= 1) AND (length(title) <= 500))),
    CONSTRAINT job_postings_version_check CHECK ((version > 0)),
    CONSTRAINT posting_source_shape CHECK ((((source_config_id IS NULL) AND (external_id IS NULL) AND (content_sha256 IS NULL) AND (last_observed_at IS NULL)) OR ((source_config_id IS NOT NULL) AND (external_id IS NOT NULL) AND ((length(external_id) >= 1) AND (length(external_id) <= 200)) AND (content_sha256 IS NOT NULL) AND (last_observed_at IS NOT NULL))))
);


ALTER TABLE bonafides.job_postings OWNER TO bonafides_migrator;

-- Name: job_postings_fts_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX job_postings_fts_idx ON bonafides.job_postings USING gin (search_vector);

-- Name: job_postings_list_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX job_postings_list_idx ON bonafides.job_postings USING btree (owner_id, created_at, id);

-- Name: job_source_observations immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.job_source_observations FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: job_source_observations job_source_observations_owner_id_posting_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_source_observations
    ADD CONSTRAINT job_source_observations_owner_id_posting_id_fkey FOREIGN KEY (owner_id, posting_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE CASCADE;

-- Name: job_source_observations job_source_observations_owner_id_source_config_id_external__key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_source_observations
    ADD CONSTRAINT job_source_observations_owner_id_source_config_id_external__key UNIQUE (owner_id, source_config_id, external_id, policy_version, content_sha256);

-- Name: job_source_observations job_source_observations_owner_id_source_config_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_source_observations
    ADD CONSTRAINT job_source_observations_owner_id_source_config_id_fkey FOREIGN KEY (owner_id, source_config_id) REFERENCES bonafides.source_configs(owner_id, id) ON DELETE RESTRICT;

-- Name: job_source_observations job_source_observations_owner_id_source_config_id_policy_v_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_source_observations
    ADD CONSTRAINT job_source_observations_owner_id_source_config_id_policy_v_fkey FOREIGN KEY (owner_id, source_config_id, policy_version, policy_review_id) REFERENCES bonafides.source_policy_reviews(owner_id, source_config_id, policy_version, id) ON DELETE RESTRICT;

-- Name: job_source_observations job_source_observations_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.job_source_observations
    ADD CONSTRAINT job_source_observations_pkey PRIMARY KEY (owner_id, id);

-- Name: job_source_observations observation_source_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER observation_source_guard BEFORE INSERT ON bonafides.job_source_observations FOR EACH ROW EXECUTE FUNCTION bonafides.check_observation_source();

-- Name: job_source_observations; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.job_source_observations (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    posting_id uuid NOT NULL,
    source_config_id uuid NOT NULL,
    external_id text NOT NULL,
    content_sha256 text NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    source_url text NOT NULL,
    snapshot jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    policy_version bigint DEFAULT 1 NOT NULL,
    policy_review_id uuid,
    CONSTRAINT job_source_observations_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT job_source_observations_policy_version_check CHECK ((policy_version > 0)),
    CONSTRAINT job_source_observations_snapshot_check CHECK ((jsonb_typeof(snapshot) = 'object'::text))
);


ALTER TABLE bonafides.job_source_observations OWNER TO bonafides_migrator;

-- Name: links_evidence_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX links_evidence_idx ON bonafides.claim_evidence_links USING btree (owner_id, evidence_revision_id);

-- Name: local_model_reservations local_model_reservations_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.local_model_reservations
    ADD CONSTRAINT local_model_reservations_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE SET NULL;

-- Name: local_model_reservations local_model_reservations_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.local_model_reservations
    ADD CONSTRAINT local_model_reservations_pkey PRIMARY KEY (singleton);

-- Name: local_model_reservations; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.local_model_reservations (
    singleton boolean DEFAULT true NOT NULL,
    owner_id uuid,
    workflow_run_id uuid,
    fence bigint,
    reserved_until timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT local_model_reservations_check CHECK (((owner_id IS NULL) = (workflow_run_id IS NULL))),
    CONSTRAINT local_model_reservations_singleton_check CHECK (singleton)
);


ALTER TABLE bonafides.local_model_reservations OWNER TO bonafides_migrator;

-- Name: local_model_route_blocks local_model_route_blocks_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.local_model_route_blocks
    ADD CONSTRAINT local_model_route_blocks_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: local_model_route_blocks local_model_route_blocks_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.local_model_route_blocks
    ADD CONSTRAINT local_model_route_blocks_pkey PRIMARY KEY (owner_id, route_sha256, stage);

-- Name: local_model_route_blocks; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.local_model_route_blocks (
    owner_id uuid NOT NULL,
    route_sha256 text NOT NULL,
    stage text NOT NULL,
    error_code text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT local_model_route_blocks_route_sha256_check CHECK ((route_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT local_model_route_blocks_stage_check CHECK ((stage = ANY (ARRAY['extract_posting'::text, 'match_requirements'::text, 'generate_documents'::text, 'regenerate_section'::text])))
);


ALTER TABLE bonafides.local_model_route_blocks OWNER TO bonafides_migrator;

-- Name: maintenance_recoveries maintenance_recoveries_control_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.maintenance_recoveries
    ADD CONSTRAINT maintenance_recoveries_control_id_fkey FOREIGN KEY (control_id) REFERENCES bonafides.installation_controls(id) ON DELETE CASCADE;

-- Name: maintenance_recoveries maintenance_recoveries_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.maintenance_recoveries
    ADD CONSTRAINT maintenance_recoveries_pkey PRIMARY KEY (id);

-- Name: maintenance_recoveries; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.maintenance_recoveries (
    id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    control_id uuid,
    previous_holder uuid,
    previous_fence bigint,
    holder uuid NOT NULL,
    control_fence bigint,
    execution_guard_id uuid NOT NULL,
    parser_nonce text NOT NULL,
    renderer_nonce text NOT NULL,
    process_proof text NOT NULL,
    cleared_activities bigint NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deadline_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    outcome text,
    CONSTRAINT maintenance_recoveries_check CHECK (((outcome IS NULL) = (finished_at IS NULL))),
    CONSTRAINT maintenance_recoveries_check1 CHECK (((control_id IS NULL) = (previous_holder IS NULL))),
    CONSTRAINT maintenance_recoveries_check2 CHECK (((control_id IS NULL) = (previous_fence IS NULL))),
    CONSTRAINT maintenance_recoveries_check3 CHECK (((control_id IS NULL) = (control_fence IS NULL))),
    CONSTRAINT maintenance_recoveries_check4 CHECK ((parser_nonce <> renderer_nonce)),
    CONSTRAINT maintenance_recoveries_check5 CHECK (((deadline_at > started_at) AND (deadline_at <= (started_at + '00:15:00'::interval)))),
    CONSTRAINT maintenance_recoveries_cleared_activities_check CHECK ((cleared_activities >= 0)),
    CONSTRAINT maintenance_recoveries_outcome_check CHECK ((outcome = ANY (ARRAY['ABORTED'::text, 'PURGE_RESUMED'::text, 'ADMISSION_RECOVERED'::text, 'SUPERSEDED'::text]))),
    CONSTRAINT maintenance_recoveries_parser_nonce_check CHECK ((parser_nonce ~ '^[0-9a-f]{32}$'::text)),
    CONSTRAINT maintenance_recoveries_process_proof_check CHECK ((process_proof = 'exclusive-kernel-flock/v1'::text)),
    CONSTRAINT maintenance_recoveries_renderer_nonce_check CHECK ((renderer_nonce ~ '^[0-9a-f]{32}$'::text))
);


ALTER TABLE bonafides.maintenance_recoveries OWNER TO bonafides_migrator;

-- Name: match_assessment_historical_model_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX match_assessment_historical_model_idx ON bonafides.match_assessments USING btree (owner_id, historical_model_run_id) WHERE (historical_model_run_id IS NOT NULL);

-- Name: match_assessments immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.match_assessments FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: match_assessments match_assessments_owner_id_historical_model_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_owner_id_historical_model_run_id_fkey FOREIGN KEY (owner_id, historical_model_run_id) REFERENCES bonafides.portable_model_history(owner_id, id) ON DELETE RESTRICT;

-- Name: match_assessments match_assessments_owner_id_model_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_owner_id_model_run_id_fkey FOREIGN KEY (owner_id, model_run_id) REFERENCES bonafides.model_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: match_assessments match_assessments_owner_id_posting_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_owner_id_posting_id_fkey FOREIGN KEY (owner_id, posting_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE CASCADE;

-- Name: match_assessments match_assessments_owner_id_posting_id_requirements_revisio_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_owner_id_posting_id_requirements_revisio_fkey FOREIGN KEY (owner_id, posting_id, requirements_revision_id) REFERENCES bonafides.posting_requirement_revisions(owner_id, posting_id, id) ON DELETE RESTRICT;

-- Name: match_assessments match_assessments_owner_id_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_owner_id_profile_revision_id_fkey FOREIGN KEY (owner_id, profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: match_assessments match_assessments_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.match_assessments
    ADD CONSTRAINT match_assessments_pkey PRIMARY KEY (owner_id, id);

-- Name: match_assessments; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.match_assessments (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    posting_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    model_run_id uuid,
    posting_version bigint NOT NULL,
    hard_constraints jsonb NOT NULL,
    evidence_coverage jsonb NOT NULL,
    explanation text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    requirements_revision_id uuid,
    hard_eligibility text DEFAULT 'unknown'::text NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    historical_model_run_id uuid,
    CONSTRAINT match_assessments_check CHECK (((model_run_id IS NULL) OR (historical_model_run_id IS NULL))),
    CONSTRAINT match_assessments_evidence_coverage_check CHECK ((jsonb_typeof(evidence_coverage) = 'array'::text)),
    CONSTRAINT match_assessments_hard_constraints_check CHECK ((jsonb_typeof(hard_constraints) = 'array'::text)),
    CONSTRAINT match_assessments_hard_eligibility_check CHECK ((hard_eligibility = ANY (ARRAY['met'::text, 'not_met'::text, 'unknown'::text]))),
    CONSTRAINT match_assessments_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text))
);


ALTER TABLE bonafides.match_assessments OWNER TO bonafides_migrator;

-- Name: model_runs model_runs_owner_id_hosted_consent_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.model_runs
    ADD CONSTRAINT model_runs_owner_id_hosted_consent_id_fkey FOREIGN KEY (owner_id, hosted_consent_id) REFERENCES bonafides.hosted_egress_consents(owner_id, id) ON DELETE RESTRICT;

-- Name: model_runs model_runs_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.model_runs
    ADD CONSTRAINT model_runs_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: model_runs model_runs_owner_id_workflow_unique; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.model_runs
    ADD CONSTRAINT model_runs_owner_id_workflow_unique UNIQUE (owner_id, id, workflow_run_id);

-- Name: model_runs model_runs_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.model_runs
    ADD CONSTRAINT model_runs_pkey PRIMARY KEY (owner_id, id);

-- Name: model_runs; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.model_runs (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    provider_key text NOT NULL,
    model_id text NOT NULL,
    model_revision text,
    task_kind text NOT NULL,
    prompt_version text NOT NULL,
    input_sha256 text NOT NULL,
    output_sha256 text,
    parameters jsonb NOT NULL,
    egress_class text NOT NULL,
    input_tokens bigint,
    output_tokens bigint,
    reserved_tokens bigint NOT NULL,
    reserved_cost_microunits bigint NOT NULL,
    actual_cost_microunits bigint,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    hosted_consent_id uuid,
    usage_status text DEFAULT 'unknown'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT model_runs_actual_cost_microunits_check CHECK ((actual_cost_microunits >= 0)),
    CONSTRAINT model_runs_check CHECK (((egress_class <> 'hosted_opt_in'::text) OR (hosted_consent_id IS NOT NULL))),
    CONSTRAINT model_runs_egress_class_check CHECK ((egress_class = ANY (ARRAY['local'::text, 'hosted_opt_in'::text]))),
    CONSTRAINT model_runs_input_sha256_check CHECK ((input_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT model_runs_input_tokens_check CHECK ((input_tokens >= 0)),
    CONSTRAINT model_runs_metadata_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT model_runs_output_tokens_check CHECK ((output_tokens >= 0)),
    CONSTRAINT model_runs_parameters_check CHECK ((jsonb_typeof(parameters) = 'object'::text)),
    CONSTRAINT model_runs_reserved_cost_microunits_check CHECK ((reserved_cost_microunits >= 0)),
    CONSTRAINT model_runs_reserved_tokens_check CHECK ((reserved_tokens >= 0)),
    CONSTRAINT model_runs_status_check CHECK ((status = ANY (ARRAY['reserved'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'unknown_usage'::text]))),
    CONSTRAINT model_runs_usage_status_check CHECK ((usage_status = ANY (ARRAY['known'::text, 'unknown'::text])))
);


ALTER TABLE bonafides.model_runs OWNER TO bonafides_migrator;

-- Name: model_runs_consent_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX model_runs_consent_idx ON bonafides.model_runs USING btree (owner_id, hosted_consent_id);

-- Name: model_runs_workflow_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX model_runs_workflow_idx ON bonafides.model_runs USING btree (owner_id, workflow_run_id);

-- Name: notes_application_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX notes_application_idx ON bonafides.application_notes USING btree (owner_id, application_id, created_at, id);

-- Name: oauth_transactions oauth_transactions_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.oauth_transactions
    ADD CONSTRAINT oauth_transactions_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: oauth_transactions oauth_transactions_owner_id_session_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.oauth_transactions
    ADD CONSTRAINT oauth_transactions_owner_id_session_id_fkey FOREIGN KEY (owner_id, session_id) REFERENCES bonafides.sessions(owner_id, id) ON DELETE CASCADE;

-- Name: oauth_transactions oauth_transactions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.oauth_transactions
    ADD CONSTRAINT oauth_transactions_pkey PRIMARY KEY (owner_id, id);

-- Name: oauth_transactions oauth_transactions_state_sha256_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.oauth_transactions
    ADD CONSTRAINT oauth_transactions_state_sha256_key UNIQUE (state_sha256);

-- Name: oauth_transactions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.oauth_transactions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    state_sha256 text NOT NULL,
    session_id uuid NOT NULL,
    provider_key text NOT NULL,
    pkce_ciphertext bytea NOT NULL,
    key_id text NOT NULL,
    nonce bytea NOT NULL,
    redirect_uri text NOT NULL,
    requested_scopes text[] NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT oauth_transactions_check CHECK ((expires_at > created_at)),
    CONSTRAINT oauth_transactions_nonce_check CHECK ((octet_length(nonce) = 12)),
    CONSTRAINT oauth_transactions_state_sha256_check CHECK ((state_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.oauth_transactions OWNER TO bonafides_migrator;

-- Name: observation_history_cursor; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX observation_history_cursor ON bonafides.job_source_observations USING btree (owner_id, posting_id, created_at DESC, id DESC);

-- Name: observations_posting_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX observations_posting_idx ON bonafides.job_source_observations USING btree (owner_id, posting_id, fetched_at DESC);

-- Name: one_active_source_refresh; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX one_active_source_refresh ON bonafides.workflow_runs USING btree (owner_id, source_config_id) WHERE ((kind = 'source_refresh'::text) AND (state = ANY (ARRAY['pending'::bonafides.workflow_state, 'running'::bonafides.workflow_state, 'retryable_failure'::bonafides.workflow_state, 'waiting_for_approval'::bonafides.workflow_state])));

-- Name: one_base_resume_per_owner_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX one_base_resume_per_owner_idx ON bonafides.document_drafts USING btree (owner_id) WHERE (scope = 'base'::text);

-- Name: opportunity_disposition_catalog; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX opportunity_disposition_catalog ON bonafides.saved_opportunities USING btree (owner_id, disposition, posting_id);

-- Name: outbox_delivery_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX outbox_delivery_idx ON bonafides.outbox_events USING btree (available_at, created_at, id) WHERE (delivered_at IS NULL);

-- Name: outbox_events outbox_events_owner_id_dedupe_key_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.outbox_events
    ADD CONSTRAINT outbox_events_owner_id_dedupe_key_key UNIQUE (owner_id, dedupe_key);

-- Name: outbox_events outbox_events_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.outbox_events
    ADD CONSTRAINT outbox_events_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (owner_id, id);

-- Name: outbox_events; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.outbox_events (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    aggregate_version bigint NOT NULL,
    event_type text NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    payload jsonb NOT NULL,
    dedupe_key text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    attempt integer DEFAULT 0 NOT NULL,
    lease_holder uuid,
    lease_expires_at timestamp with time zone,
    fence bigint DEFAULT 0 NOT NULL,
    delivered_at timestamp with time zone,
    last_error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outbox_events_aggregate_version_check CHECK ((aggregate_version > 0)),
    CONSTRAINT outbox_events_attempt_check CHECK ((attempt >= 0)),
    CONSTRAINT outbox_events_check CHECK (((lease_holder IS NULL) = (lease_expires_at IS NULL))),
    CONSTRAINT outbox_events_fence_check CHECK ((fence >= 0)),
    CONSTRAINT outbox_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT outbox_events_schema_version_check CHECK ((schema_version = 1))
);


ALTER TABLE bonafides.outbox_events OWNER TO bonafides_migrator;

-- Name: owner_settings owner_settings_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.owner_settings
    ADD CONSTRAINT owner_settings_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: owner_settings owner_settings_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.owner_settings
    ADD CONSTRAINT owner_settings_pkey PRIMARY KEY (owner_id);

-- Name: owner_settings; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.owner_settings (
    owner_id uuid NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    privacy_mode text DEFAULT 'local_only'::text NOT NULL,
    timezone text DEFAULT 'UTC'::text NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_version bigint DEFAULT 1 NOT NULL,
    CONSTRAINT owner_settings_privacy_mode_check CHECK ((privacy_mode = ANY (ARRAY['local_only'::text, 'hosted_opt_in'::text]))),
    CONSTRAINT owner_settings_settings_check CHECK ((jsonb_typeof(settings) = 'object'::text)),
    CONSTRAINT owner_settings_version_check CHECK ((version > 0)),
    CONSTRAINT owner_settings_workspace_version_check CHECK ((workspace_version > 0))
);


ALTER TABLE bonafides.owner_settings OWNER TO bonafides_migrator;

-- Name: owners owners_login_name_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.owners
    ADD CONSTRAINT owners_login_name_key UNIQUE (login_name);

-- Name: owners owners_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.owners
    ADD CONSTRAINT owners_pkey PRIMARY KEY (id);

-- Name: owners owners_singleton_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.owners
    ADD CONSTRAINT owners_singleton_key UNIQUE (singleton);

-- Name: owners; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.owners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    singleton boolean DEFAULT true NOT NULL,
    login_name text NOT NULL,
    password_hash text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    auth_epoch bigint DEFAULT 1 NOT NULL,
    CONSTRAINT owners_auth_epoch_check CHECK ((auth_epoch > 0)),
    CONSTRAINT owners_login_name_check CHECK (((length(login_name) >= 1) AND (length(login_name) <= 120))),
    CONSTRAINT owners_singleton_check CHECK (singleton),
    CONSTRAINT owners_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.owners OWNER TO bonafides_migrator;

-- Name: portable_export_expiry_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX portable_export_expiry_idx ON bonafides.portable_exports USING btree (expires_at, workflow_run_id) WHERE (state <> 'expired'::text);

-- Name: portable_exports portable_exports_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_exports
    ADD CONSTRAINT portable_exports_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: portable_exports portable_exports_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_exports
    ADD CONSTRAINT portable_exports_pkey PRIMARY KEY (owner_id, workflow_run_id);

-- Name: portable_exports; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_exports (
    owner_id uuid NOT NULL,
    workflow_run_id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    archive_sha256 text NOT NULL,
    manifest_sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone DEFAULT (clock_timestamp() + '24:00:00'::interval) NOT NULL,
    CONSTRAINT portable_exports_archive_sha256_check CHECK ((archive_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT portable_exports_byte_length_check CHECK (((byte_length >= 1) AND (byte_length <= '671109611520'::bigint))),
    CONSTRAINT portable_exports_check CHECK ((expires_at > created_at)),
    CONSTRAINT portable_exports_maintenance_epoch_check CHECK ((maintenance_epoch > 0)),
    CONSTRAINT portable_exports_manifest_sha256_check CHECK ((manifest_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT portable_exports_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'ready'::text, 'expired'::text])))
);


ALTER TABLE bonafides.portable_exports OWNER TO bonafides_migrator;

-- Name: portable_model_history immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.portable_model_history FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: portable_model_history portable_model_history_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_model_history
    ADD CONSTRAINT portable_model_history_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: portable_model_history portable_model_history_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_model_history
    ADD CONSTRAINT portable_model_history_pkey PRIMARY KEY (owner_id, id);

-- Name: portable_model_history; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_model_history (
    owner_id uuid NOT NULL,
    id uuid NOT NULL,
    source_namespace uuid NOT NULL,
    source_owner_id uuid NOT NULL,
    record jsonb NOT NULL,
    record_sha256 text NOT NULL,
    imported_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT portable_model_history_check CHECK (((record ->> 'id'::text) = (id)::text)),
    CONSTRAINT portable_model_history_check1 CHECK (((record ->> 'owner_id'::text) = (source_owner_id)::text)),
    CONSTRAINT portable_model_history_record_check CHECK ((jsonb_typeof(record) = 'object'::text)),
    CONSTRAINT portable_model_history_record_check1 CHECK (((record ? 'workflow_run_id'::text) AND (jsonb_typeof((record -> 'workflow_run_id'::text)) = 'string'::text))),
    CONSTRAINT portable_model_history_record_sha256_check CHECK ((record_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.portable_model_history OWNER TO bonafides_migrator;

-- Name: portable_preview_chunks portable_preview_chunk_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER portable_preview_chunk_guard BEFORE INSERT ON bonafides.portable_preview_chunks FOR EACH ROW EXECUTE FUNCTION bonafides.guard_portable_preview_chunk();

-- Name: portable_preview_chunks portable_preview_chunks_owner_id_preview_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_preview_chunks
    ADD CONSTRAINT portable_preview_chunks_owner_id_preview_id_fkey FOREIGN KEY (owner_id, preview_id) REFERENCES bonafides.portable_previews(owner_id, id) ON DELETE CASCADE;

-- Name: portable_preview_chunks portable_preview_chunks_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_preview_chunks
    ADD CONSTRAINT portable_preview_chunks_pkey PRIMARY KEY (owner_id, preview_id, chunk_no);

-- Name: portable_preview_chunks; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_preview_chunks (
    owner_id uuid NOT NULL,
    preview_id uuid NOT NULL,
    chunk_no integer NOT NULL,
    payload bytea NOT NULL,
    CONSTRAINT portable_preview_chunks_chunk_no_check CHECK (((chunk_no >= 1) AND (chunk_no <= 640019))),
    CONSTRAINT portable_preview_chunks_payload_check CHECK (((octet_length(payload) >= 1) AND (octet_length(payload) <= 1048576)))
);


ALTER TABLE bonafides.portable_preview_chunks OWNER TO bonafides_migrator;

-- Name: portable_previews portable_preview_chunks_clear; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER portable_preview_chunks_clear AFTER UPDATE OF candidate_bytes ON bonafides.portable_previews FOR EACH ROW WHEN ((new.candidate_bytes IS NULL)) EXECUTE FUNCTION bonafides.clear_portable_preview_chunks();

-- Name: portable_previews portable_previews_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_previews
    ADD CONSTRAINT portable_previews_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: portable_previews portable_previews_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_previews
    ADD CONSTRAINT portable_previews_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: portable_previews portable_previews_owner_id_workflow_run_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_previews
    ADD CONSTRAINT portable_previews_owner_id_workflow_run_id_key UNIQUE (owner_id, workflow_run_id);

-- Name: portable_previews portable_previews_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_previews
    ADD CONSTRAINT portable_previews_pkey PRIMARY KEY (owner_id, id);

-- Name: portable_previews; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_previews (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    action_kind text NOT NULL,
    plan jsonb NOT NULL,
    preview_sha256 text NOT NULL,
    candidate_bytes bytea,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    cleared_at timestamp with time zone,
    clear_reason text,
    retained_at timestamp with time zone DEFAULT clock_timestamp(),
    CONSTRAINT portable_previews_action_kind_check CHECK ((action_kind = ANY (ARRAY['replace_workspace'::text, 'privacy_delete'::text]))),
    CONSTRAINT portable_previews_candidate_bytes_check CHECK (((octet_length(candidate_bytes) >= 1) AND (octet_length(candidate_bytes) <= 1048576))),
    CONSTRAINT portable_previews_check CHECK (((expires_at > created_at) AND (expires_at <= (created_at + '00:10:00'::interval)))),
    CONSTRAINT portable_previews_check1 CHECK (((cleared_at IS NULL) = (clear_reason IS NULL))),
    CONSTRAINT portable_previews_check2 CHECK ((((action_kind = 'replace_workspace'::text) AND (cleared_at IS NULL)) = (candidate_bytes IS NOT NULL))),
    CONSTRAINT portable_previews_clear_reason_check CHECK ((clear_reason = ANY (ARRAY['expired'::text, 'cancelled'::text, 'erased'::text, 'installation_restored'::text]))),
    CONSTRAINT portable_previews_plan_check CHECK (((jsonb_typeof(plan) = 'object'::text) AND (octet_length((plan)::text) <= 262144))),
    CONSTRAINT portable_previews_preview_sha256_check CHECK ((preview_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.portable_previews OWNER TO bonafides_migrator;

-- Name: portable_previews_one_live_owner; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX portable_previews_one_live_owner ON bonafides.portable_previews USING btree (owner_id) WHERE (cleared_at IS NULL);

-- Name: portable_purge_items portable_purge_items_control_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_purge_items
    ADD CONSTRAINT portable_purge_items_control_id_fkey FOREIGN KEY (control_id) REFERENCES bonafides.portable_purges(control_id) ON DELETE CASCADE;

-- Name: portable_purge_items portable_purge_items_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_purge_items
    ADD CONSTRAINT portable_purge_items_pkey PRIMARY KEY (control_id, kind, storage_key);

-- Name: portable_purge_items; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_purge_items (
    control_id uuid NOT NULL,
    kind text NOT NULL,
    storage_key text NOT NULL,
    sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    resolved_at timestamp with time zone,
    outcome text,
    CONSTRAINT portable_purge_items_byte_length_check CHECK ((byte_length >= 0)),
    CONSTRAINT portable_purge_items_check CHECK (((resolved_at IS NULL) = (outcome IS NULL))),
    CONSTRAINT portable_purge_items_check1 CHECK ((((kind = 'cas'::text) AND (storage_key = (("left"(sha256, 2) || '/'::text) || sha256))) OR ((kind = 'cas_staging'::text) AND (storage_key ~ '^[0-9a-f]{2}/\.staging-[0-9a-f]{32}$'::text)) OR ((kind = ANY (ARRAY['export'::text, 'candidate'::text])) AND (storage_key ~ '^[0-9a-f-]{36}\.zip$'::text)))),
    CONSTRAINT portable_purge_items_kind_check CHECK ((kind = ANY (ARRAY['cas'::text, 'cas_staging'::text, 'export'::text, 'candidate'::text]))),
    CONSTRAINT portable_purge_items_outcome_check CHECK ((outcome = ANY (ARRAY['removed'::text, 'retained'::text]))),
    CONSTRAINT portable_purge_items_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.portable_purge_items OWNER TO bonafides_migrator;

-- Name: portable_purges portable_erasure_clears_previews; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER portable_erasure_clears_previews AFTER INSERT ON bonafides.portable_purges FOR EACH ROW EXECUTE FUNCTION bonafides.clear_erased_portable_previews();

-- Name: portable_purges portable_purges_control_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_purges
    ADD CONSTRAINT portable_purges_control_id_fkey FOREIGN KEY (control_id) REFERENCES bonafides.installation_controls(id);

-- Name: portable_purges portable_purges_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_purges
    ADD CONSTRAINT portable_purges_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: portable_purges portable_purges_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_purges
    ADD CONSTRAINT portable_purges_pkey PRIMARY KEY (control_id);

-- Name: portable_purges; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_purges (
    control_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    scope text NOT NULL,
    application_id uuid,
    committed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT portable_purges_check CHECK (((scope = 'application'::text) = (application_id IS NOT NULL))),
    CONSTRAINT portable_purges_scope_check CHECK ((scope = ANY (ARRAY['application'::text, 'workspace'::text])))
);


ALTER TABLE bonafides.portable_purges OWNER TO bonafides_migrator;

-- Name: portable_staged_files portable_staged_files_control_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_staged_files
    ADD CONSTRAINT portable_staged_files_control_id_fkey FOREIGN KEY (control_id) REFERENCES bonafides.installation_controls(id) ON DELETE CASCADE;

-- Name: portable_staged_files portable_staged_files_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.portable_staged_files
    ADD CONSTRAINT portable_staged_files_pkey PRIMARY KEY (control_id, storage_key);

-- Name: portable_staged_files; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.portable_staged_files (
    control_id uuid NOT NULL,
    storage_key text NOT NULL,
    staging_name text NOT NULL,
    sha256 text NOT NULL,
    byte_length bigint NOT NULL,
    CONSTRAINT portable_staged_files_byte_length_check CHECK (((byte_length > 0) AND (byte_length <= 67108864))),
    CONSTRAINT portable_staged_files_check CHECK ((storage_key = (("left"(sha256, 2) || '/'::text) || sha256))),
    CONSTRAINT portable_staged_files_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT portable_staged_files_staging_name_check CHECK ((staging_name ~ '^\.staging-[0-9a-f]{32}$'::text))
);


ALTER TABLE bonafides.portable_staged_files OWNER TO bonafides_migrator;

-- Name: posting_duplicate_candidates; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX posting_duplicate_candidates ON bonafides.job_postings USING btree (owner_id, md5(normalized_company), md5(normalized_title), md5(normalized_location), created_at DESC, id DESC);

-- Name: posting_duplicate_decisions posting_duplicate_decisions_owner_id_candidate_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_duplicate_decisions
    ADD CONSTRAINT posting_duplicate_decisions_owner_id_candidate_id_fkey FOREIGN KEY (owner_id, candidate_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE RESTRICT;

-- Name: posting_duplicate_decisions posting_duplicate_decisions_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_duplicate_decisions
    ADD CONSTRAINT posting_duplicate_decisions_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: posting_duplicate_decisions posting_duplicate_decisions_owner_id_posting_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_duplicate_decisions
    ADD CONSTRAINT posting_duplicate_decisions_owner_id_posting_id_fkey FOREIGN KEY (owner_id, posting_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE RESTRICT;

-- Name: posting_duplicate_decisions posting_duplicate_decisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_duplicate_decisions
    ADD CONSTRAINT posting_duplicate_decisions_pkey PRIMARY KEY (owner_id, posting_id, candidate_id);

-- Name: posting_duplicate_decisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.posting_duplicate_decisions (
    owner_id uuid NOT NULL,
    posting_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    decision text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT posting_duplicate_decisions_check CHECK ((posting_id < candidate_id)),
    CONSTRAINT posting_duplicate_decisions_decision_check CHECK ((decision = ANY (ARRAY['suspected'::text, 'distinct'::text]))),
    CONSTRAINT posting_duplicate_decisions_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.posting_duplicate_decisions OWNER TO bonafides_migrator;

-- Name: posting_freshness_catalog; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX posting_freshness_catalog ON bonafides.job_postings USING btree (owner_id, last_observed_at DESC, id DESC);

-- Name: posting_requirement_revisions immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.posting_requirement_revisions FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: posting_requirement_revisions posting_requirement_revisions_owner_id_posting_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_requirement_revisions
    ADD CONSTRAINT posting_requirement_revisions_owner_id_posting_id_fkey FOREIGN KEY (owner_id, posting_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE RESTRICT;

-- Name: posting_requirement_revisions posting_requirement_revisions_owner_id_posting_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_requirement_revisions
    ADD CONSTRAINT posting_requirement_revisions_owner_id_posting_id_id_key UNIQUE (owner_id, posting_id, id);

-- Name: posting_requirement_revisions posting_requirement_revisions_owner_id_posting_id_revision__key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_requirement_revisions
    ADD CONSTRAINT posting_requirement_revisions_owner_id_posting_id_revision__key UNIQUE (owner_id, posting_id, revision_no);

-- Name: posting_requirement_revisions posting_requirement_revisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.posting_requirement_revisions
    ADD CONSTRAINT posting_requirement_revisions_pkey PRIMARY KEY (owner_id, id);

-- Name: posting_requirement_revisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.posting_requirement_revisions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    posting_id uuid NOT NULL,
    revision_no bigint NOT NULL,
    source_text text NOT NULL,
    source_sha256 text NOT NULL,
    posting_content_sha256 text NOT NULL,
    requirements jsonb NOT NULL,
    confirmed boolean NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT posting_requirement_revisions_confirmed_check CHECK (confirmed),
    CONSTRAINT posting_requirement_revisions_posting_content_sha256_check CHECK ((posting_content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT posting_requirement_revisions_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT posting_requirement_revisions_requirements_check CHECK ((jsonb_typeof(requirements) = 'array'::text)),
    CONSTRAINT posting_requirement_revisions_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT posting_requirement_revisions_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.posting_requirement_revisions OWNER TO bonafides_migrator;

-- Name: posting_source_catalog; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX posting_source_catalog ON bonafides.job_postings USING btree (owner_id, source_config_id, created_at DESC, id DESC);

-- Name: posting_source_identity; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE UNIQUE INDEX posting_source_identity ON bonafides.job_postings USING btree (owner_id, source_config_id, external_id);

-- Name: posting_status_catalog; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX posting_status_catalog ON bonafides.job_postings USING btree (owner_id, status, created_at DESC, id DESC);

-- Name: professional_claims professional_claims_owner_id_id_current_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.professional_claims
    ADD CONSTRAINT professional_claims_owner_id_id_current_revision_id_fkey FOREIGN KEY (owner_id, id, current_revision_id) REFERENCES bonafides.claim_revisions(owner_id, claim_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: professional_claims professional_claims_owner_id_profile_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.professional_claims
    ADD CONSTRAINT professional_claims_owner_id_profile_id_fkey FOREIGN KEY (owner_id, profile_id) REFERENCES bonafides.profiles(owner_id, id) ON DELETE CASCADE;

-- Name: professional_claims professional_claims_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.professional_claims
    ADD CONSTRAINT professional_claims_pkey PRIMARY KEY (owner_id, id);

-- Name: professional_claims; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.professional_claims (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id uuid NOT NULL,
    kind text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    current_revision_id uuid,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT professional_claims_kind_check CHECK ((kind = ANY (ARRAY['identity'::text, 'experience'::text, 'education'::text, 'skill'::text, 'project'::text, 'certification'::text, 'preference'::text, 'other'::text, 'language'::text]))),
    CONSTRAINT professional_claims_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.professional_claims OWNER TO bonafides_migrator;

-- Name: profile_revision_claims immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.profile_revision_claims FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: profile_revision_claims profile_revision_claims_owner_id_claim_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_claims
    ADD CONSTRAINT profile_revision_claims_owner_id_claim_revision_id_fkey FOREIGN KEY (owner_id, claim_revision_id) REFERENCES bonafides.claim_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: profile_revision_claims profile_revision_claims_owner_id_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_claims
    ADD CONSTRAINT profile_revision_claims_owner_id_profile_revision_id_fkey FOREIGN KEY (owner_id, profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: profile_revision_claims profile_revision_claims_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_claims
    ADD CONSTRAINT profile_revision_claims_pkey PRIMARY KEY (owner_id, profile_revision_id, claim_revision_id);

-- Name: profile_revision_claims; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.profile_revision_claims (
    owner_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    claim_revision_id uuid NOT NULL
);


ALTER TABLE bonafides.profile_revision_claims OWNER TO bonafides_migrator;

-- Name: profile_revision_evidence immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.profile_revision_evidence FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: profile_revision_evidence profile_revision_evidence_owner_id_evidence_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_evidence
    ADD CONSTRAINT profile_revision_evidence_owner_id_evidence_revision_id_fkey FOREIGN KEY (owner_id, evidence_revision_id) REFERENCES bonafides.evidence_revisions(owner_id, id) ON DELETE RESTRICT;

-- Name: profile_revision_evidence profile_revision_evidence_owner_id_profile_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_evidence
    ADD CONSTRAINT profile_revision_evidence_owner_id_profile_revision_id_fkey FOREIGN KEY (owner_id, profile_revision_id) REFERENCES bonafides.profile_revisions(owner_id, id) ON DELETE CASCADE;

-- Name: profile_revision_evidence profile_revision_evidence_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revision_evidence
    ADD CONSTRAINT profile_revision_evidence_pkey PRIMARY KEY (owner_id, profile_revision_id, evidence_revision_id);

-- Name: profile_revision_evidence; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.profile_revision_evidence (
    owner_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    evidence_revision_id uuid NOT NULL
);


ALTER TABLE bonafides.profile_revision_evidence OWNER TO bonafides_migrator;

-- Name: profile_revisions immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.profile_revisions FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: profile_revisions profile_revisions_owner_id_profile_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revisions
    ADD CONSTRAINT profile_revisions_owner_id_profile_id_fkey FOREIGN KEY (owner_id, profile_id) REFERENCES bonafides.profiles(owner_id, id) ON DELETE CASCADE;

-- Name: profile_revisions profile_revisions_owner_id_profile_id_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revisions
    ADD CONSTRAINT profile_revisions_owner_id_profile_id_id_key UNIQUE (owner_id, profile_id, id);

-- Name: profile_revisions profile_revisions_owner_id_profile_id_revision_no_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revisions
    ADD CONSTRAINT profile_revisions_owner_id_profile_id_revision_no_key UNIQUE (owner_id, profile_id, revision_no);

-- Name: profile_revisions profile_revisions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profile_revisions
    ADD CONSTRAINT profile_revisions_pkey PRIMARY KEY (owner_id, id);

-- Name: profile_revisions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.profile_revisions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id uuid NOT NULL,
    revision_no bigint NOT NULL,
    snapshot jsonb NOT NULL,
    content_sha256 text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT profile_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT profile_revisions_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT profile_revisions_snapshot_check CHECK ((jsonb_typeof(snapshot) = 'object'::text))
);


ALTER TABLE bonafides.profile_revisions OWNER TO bonafides_migrator;

-- Name: profiles profiles_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profiles
    ADD CONSTRAINT profiles_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: profiles profiles_owner_id_id_current_revision_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profiles
    ADD CONSTRAINT profiles_owner_id_id_current_revision_id_fkey FOREIGN KEY (owner_id, id, current_revision_id) REFERENCES bonafides.profile_revisions(owner_id, profile_id, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: profiles profiles_owner_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profiles
    ADD CONSTRAINT profiles_owner_id_key UNIQUE (owner_id);

-- Name: profiles profiles_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (owner_id, id);

-- Name: profiles; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.profiles (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    current_revision_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT profiles_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.profiles OWNER TO bonafides_migrator;

-- Name: protect_action_approval_binding(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.protect_action_approval_binding() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.action_kind,NEW.payload_sha256,
        NEW.recipient_sha256,NEW.document_revision_id,NEW.expires_at,NEW.created_at,
        NEW.intent,NEW.destination_sha256,NEW.target_version)
  IS DISTINCT FROM
    ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.action_kind,OLD.payload_sha256,
        OLD.recipient_sha256,OLD.document_revision_id,OLD.expires_at,OLD.created_at,
        OLD.intent,OLD.destination_sha256,OLD.target_version)
 THEN
  RAISE EXCEPTION 'immutable action intent' USING ERRCODE='55000';
 END IF;
 IF OLD.approved_at IS NOT NULL AND
    ROW(NEW.approved_at,NEW.approved_session_id,NEW.approved_auth_epoch)
     IS DISTINCT FROM ROW(OLD.approved_at,OLD.approved_session_id,OLD.approved_auth_epoch)
 THEN
  RAISE EXCEPTION 'immutable action approval' USING ERRCODE='55000';
 END IF;
 IF (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at)
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
 THEN
  RAISE EXCEPTION 'settled action cannot be revived' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.protect_action_approval_binding() OWNER TO bonafides_migrator;

-- Name: protect_hosted_consent_binding(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.protect_hosted_consent_binding() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'bonafides', 'pg_catalog'
    AS $$
BEGIN
 IF ROW(NEW.owner_id,NEW.id,NEW.workflow_run_id,NEW.provider_key,NEW.model_id,NEW.purpose,
        NEW.input_scope_sha256,NEW.settings_version,NEW.acknowledged_at,NEW.expires_at,
        NEW.created_at,NEW.approval_id)
  IS DISTINCT FROM
    ROW(OLD.owner_id,OLD.id,OLD.workflow_run_id,OLD.provider_key,OLD.model_id,OLD.purpose,
        OLD.input_scope_sha256,OLD.settings_version,OLD.acknowledged_at,OLD.expires_at,
        OLD.created_at,OLD.approval_id)
 THEN
  RAISE EXCEPTION 'immutable hosted consent' USING ERRCODE='55000';
 END IF;
 IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
 THEN
  RAISE EXCEPTION 'revoked hosted consent cannot be revived' USING ERRCODE='55000';
 END IF;
 IF NEW.transmission_count NOT IN (OLD.transmission_count,OLD.transmission_count+1)
 THEN
  RAISE EXCEPTION 'hosted transmission count must advance one attempt' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;


ALTER FUNCTION bonafides.protect_hosted_consent_binding() OWNER TO bonafides_migrator;

-- Name: provider_executions provider_executions_owner_id_model_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.provider_executions
    ADD CONSTRAINT provider_executions_owner_id_model_run_id_fkey FOREIGN KEY (owner_id, model_run_id) REFERENCES bonafides.model_runs(owner_id, id) ON DELETE CASCADE;

-- Name: provider_executions provider_executions_owner_id_model_run_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.provider_executions
    ADD CONSTRAINT provider_executions_owner_id_model_run_id_key UNIQUE (owner_id, model_run_id);

-- Name: provider_executions provider_executions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.provider_executions
    ADD CONSTRAINT provider_executions_pkey PRIMARY KEY (id);

-- Name: provider_executions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.provider_executions (
    id uuid NOT NULL,
    namespace_id uuid NOT NULL,
    maintenance_epoch bigint NOT NULL,
    participant_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    model_run_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    execution_guard_id uuid,
    CONSTRAINT provider_executions_maintenance_epoch_check CHECK ((maintenance_epoch >= 0))
);


ALTER TABLE bonafides.provider_executions OWNER TO bonafides_migrator;

-- Name: provider_executions_namespace_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX provider_executions_namespace_idx ON bonafides.provider_executions USING btree (namespace_id, maintenance_epoch);

-- Name: quiesce_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.quiesce_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'draining' OR c.drain_deadline_at<=clock_timestamp()
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions) THEN
  RAISE EXCEPTION 'installation not quiescent' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.installation_controls SET phase='quiescent' WHERE id=c.id;
END $$;


ALTER FUNCTION bonafides.quiesce_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: regeneration_proposal_historical_model_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX regeneration_proposal_historical_model_idx ON bonafides.document_regeneration_proposals USING btree (owner_id, historical_model_run_id) WHERE (historical_model_run_id IS NOT NULL);

-- Name: register_installation_backup(uuid, uuid, bigint, jsonb); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.register_installation_backup(p_id uuid, p_holder uuid, p_fence bigint, p_record jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose<>'backup'
 OR jsonb_typeof(p_record) IS DISTINCT FROM 'object'
 OR octet_length(p_record::text)>131072
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR (p_record->>'namespace_id')::uuid IS DISTINCT FROM c.namespace_id
 OR (p_record->>'maintenance_epoch')::bigint IS DISTINCT FROM c.maintenance_epoch
 OR (p_record->>'workspace_version')::bigint IS DISTINCT FROM (
  SELECT workspace_version FROM bonafides.owner_settings WHERE owner_id=c.owner_id)
 OR (p_record->>'installation_auth_epoch')::bigint IS DISTINCT FROM (
  SELECT auth_epoch FROM bonafides.installation_state WHERE singleton)
 OR (p_record->>'owner_auth_epoch')::bigint IS DISTINCT FROM (
  SELECT auth_epoch FROM bonafides.owners WHERE id=c.owner_id)
 OR p_record->'schema_versions' IS DISTINCT FROM (
  SELECT jsonb_object_agg(version::text,source_sha256) FROM bonafides.schema_migrations)
 THEN
  RAISE EXCEPTION 'backup generation registration refused' USING ERRCODE='55000';
 END IF;
 INSERT INTO bonafides.installation_backups(
  id,owner_id,namespace_id,maintenance_epoch,workspace_version,installation_auth_epoch,owner_auth_epoch,
  schema_versions,image_sha256,source_sha256,config_sha256,keyring_sha256,recipient_sha256,
  file_name,cipher_sha256,manifest_sha256,byte_length,captured_at
 ) VALUES(
  c.id,c.owner_id,c.namespace_id,c.maintenance_epoch,(p_record->>'workspace_version')::bigint,
  (p_record->>'installation_auth_epoch')::bigint,(p_record->>'owner_auth_epoch')::bigint,
  p_record->'schema_versions',p_record->>'image_sha256',p_record->>'source_sha256',
  p_record->>'config_sha256',p_record->>'keyring_sha256',p_record->>'recipient_sha256',
  p_record->>'file_name',p_record->>'cipher_sha256',p_record->>'manifest_sha256',
  (p_record->>'byte_length')::bigint,(p_record->>'captured_at')::timestamptz
 );
END $$;


ALTER FUNCTION bonafides.register_installation_backup(p_id uuid, p_holder uuid, p_fence bigint, p_record jsonb) OWNER TO bonafides_migrator;

-- Name: register_portable_staging(uuid, uuid, bigint, text, bigint, text); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.register_portable_staging(p_id uuid, p_holder uuid, p_fence bigint, p_sha text, p_length bigint, p_staging text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR c.purpose<>'portable_import'
 OR EXISTS(SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id) THEN
  RAISE EXCEPTION 'precommit staging authority required' USING ERRCODE='55000';
 END IF;
 INSERT INTO bonafides.portable_staged_files(control_id,storage_key,staging_name,sha256,byte_length)
 VALUES(c.id,left(p_sha,2)||'/'||p_sha,p_staging,p_sha,p_length);
END $$;


ALTER FUNCTION bonafides.register_portable_staging(p_id uuid, p_holder uuid, p_fence bigint, p_sha text, p_length bigint, p_staging text) OWNER TO bonafides_migrator;

-- Name: reject_revision_update(); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.reject_revision_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN RAISE EXCEPTION 'immutable record; append a new revision' USING ERRCODE='55000'; END $$;


ALTER FUNCTION bonafides.reject_revision_update() OWNER TO bonafides_migrator;

-- Name: release_aborted_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.release_aborted_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' OR c.result->>'outcome' IS DISTINCT FROM 'ABORTED'
 OR EXISTS(SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities) THEN
  RAISE EXCEPTION 'aborted control release refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),
  state=CASE WHEN state='running' THEN 'retryable_failure'::bonafides.workflow_state ELSE state END,
  lease_holder=NULL,lease_expires_at=NULL,next_attempt_at=clock_timestamp()
 WHERE id<>c.id AND state IN ('pending','retryable_failure','running');
 UPDATE bonafides.workflow_runs SET state='permanent_failure',result=c.result,last_error_code='maintenance_aborted',
  finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
  version=version+1,updated_at=clock_timestamp() WHERE id=c.id;
 UPDATE bonafides.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton;
 UPDATE bonafides.maintenance_recoveries SET outcome='ABORTED',finished_at=clock_timestamp()
 WHERE control_id=c.id AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL;
END $$;


ALTER FUNCTION bonafides.release_aborted_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: release_installation_control(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.release_installation_control(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.result->>'outcome'='ABORTED' THEN
  RAISE EXCEPTION 'aborted operation is not success' USING ERRCODE='55000';
 END IF;
 PERFORM bonafides.release_installation_control_engine(p_id,p_holder,p_fence);
 UPDATE bonafides.maintenance_recoveries SET outcome='PURGE_RESUMED',finished_at=clock_timestamp()
 WHERE control_id=p_id AND holder=p_holder AND control_fence=p_fence AND outcome IS NULL;
END $$;


ALTER FUNCTION bonafides.release_installation_control(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: release_installation_control_engine(uuid, uuid, bigint); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.release_installation_control_engine(p_id uuid, p_holder uuid, p_fence bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'settled' THEN
  RAISE EXCEPTION 'installation settlement required' USING ERRCODE='55000';
 END IF;
 IF c.purpose IN ('portable_import','restore_validate')
 OR (c.purpose='privacy_delete' AND c.scope->'payload'->'categories' ? 'all_workspace') THEN
  UPDATE bonafides.workflow_runs SET state='cancelled',cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
   finished_at=clock_timestamp(),lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,
   version=version+1,updated_at=clock_timestamp(),last_error_code='workspace_maintenance'
  WHERE owner_id=c.owner_id AND id<>c.id AND state NOT IN ('succeeded','cancelled','permanent_failure');
 ELSE
  -- An unchanged snapshot invalidates old executors, not queued owner intent.
  UPDATE bonafides.workflow_runs SET maintenance_epoch=c.maintenance_epoch,fence=fence+1,
   version=version+1,updated_at=clock_timestamp()
  WHERE owner_id=c.owner_id AND id<>c.id AND state IN ('pending','retryable_failure');
  UPDATE bonafides.workflow_runs SET state='retryable_failure',maintenance_epoch=c.maintenance_epoch,
   lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,
   next_attempt_at=clock_timestamp(),updated_at=clock_timestamp(),last_error_code='worker_interrupted'
  WHERE owner_id=c.owner_id AND id<>c.id AND state='running';
 END IF;
 UPDATE bonafides.workflow_runs SET state='succeeded',result=c.result,finished_at=clock_timestamp(),
  lease_holder=NULL,lease_expires_at=NULL,fence=fence+1,version=version+1,updated_at=clock_timestamp(),
  last_error_code=NULL WHERE owner_id=c.owner_id AND id=c.id;
 INSERT INTO bonafides.workflow_attempt_events(owner_id,workflow_run_id,attempt,fence,event)
 SELECT owner_id,id,attempt,fence,'succeeded' FROM bonafides.workflow_runs WHERE owner_id=c.owner_id AND id=c.id;
 UPDATE bonafides.installation_controls SET phase='released',released_at=clock_timestamp() WHERE id=c.id;
 UPDATE bonafides.installation_state SET maintenance_active=false WHERE singleton;
END $$;


ALTER FUNCTION bonafides.release_installation_control_engine(p_id uuid, p_holder uuid, p_fence bigint) OWNER TO bonafides_migrator;

-- Name: release_orphan_recovery(uuid, uuid, bigint, uuid); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.release_orphan_recovery(p_id uuid, p_namespace uuid, p_epoch bigint, p_guard uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE r bonafides.maintenance_recoveries%ROWTYPE; i bonafides.installation_state%ROWTYPE;
BEGIN
 SELECT * INTO STRICT i FROM bonafides.installation_state WHERE singleton FOR UPDATE;
 SELECT * INTO STRICT r FROM bonafides.maintenance_recoveries WHERE id=p_id FOR UPDATE;
 IF r.control_id IS NOT NULL OR r.outcome IS NOT NULL OR r.deadline_at<=clock_timestamp()
 OR r.namespace_id<>p_namespace OR i.namespace_id<>p_namespace OR NOT i.maintenance_active
 OR r.maintenance_epoch<>p_epoch OR i.maintenance_epoch<>p_epoch
 OR r.execution_guard_id<>p_guard OR i.execution_guard_id<>p_guard
 OR EXISTS(SELECT 1 FROM bonafides.installation_controls WHERE phase<>'released')
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities) THEN
  RAISE EXCEPTION 'orphan recovery fence lost' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.installation_state SET maintenance_active=false,maintenance_epoch=maintenance_epoch+1 WHERE singleton;
 UPDATE bonafides.workflow_runs SET maintenance_epoch=p_epoch+1,fence=fence+1,
  version=version+1,updated_at=clock_timestamp(),
  state=CASE WHEN state='running' THEN 'retryable_failure'::bonafides.workflow_state ELSE state END,
  lease_holder=NULL,lease_expires_at=NULL,next_attempt_at=clock_timestamp()
 WHERE state IN ('pending','retryable_failure','running');
 UPDATE bonafides.maintenance_recoveries SET outcome='ADMISSION_RECOVERED',finished_at=clock_timestamp() WHERE id=p_id;
END $$;


ALTER FUNCTION bonafides.release_orphan_recovery(p_id uuid, p_namespace uuid, p_epoch bigint, p_guard uuid) OWNER TO bonafides_migrator;

-- Name: requirements_posting_history; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX requirements_posting_history ON bonafides.posting_requirement_revisions USING btree (owner_id, posting_id, revision_no DESC);

-- Name: resolve_portable_purge_item(uuid, uuid, bigint, text, text, text); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.resolve_portable_purge_item(p_id uuid, p_holder uuid, p_fence bigint, p_kind text, p_key text, p_outcome text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
 v_live boolean;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR NOT EXISTS(SELECT 1 FROM bonafides.portable_purges
  WHERE control_id=c.id AND owner_id=c.owner_id AND completed_at IS NULL) THEN
  RAISE EXCEPTION 'purge authority required' USING ERRCODE='55000';
 END IF;
 v_live:=p_kind='cas' AND EXISTS(SELECT 1 FROM bonafides.blobs WHERE owner_id=c.owner_id AND storage_key=p_key);
 IF p_outcome NOT IN ('removed','retained') OR (p_outcome='retained') IS DISTINCT FROM v_live THEN
  RAISE EXCEPTION 'purge disposition refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.portable_purge_items SET resolved_at=clock_timestamp(),outcome=p_outcome
 WHERE control_id=c.id AND kind=p_kind AND storage_key=p_key AND resolved_at IS NULL;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'unknown or settled purge item' USING ERRCODE='55000';
 END IF;
 IF p_kind='export' THEN
  UPDATE bonafides.portable_exports SET state='expired'
  WHERE owner_id=c.owner_id AND workflow_run_id::text||'.zip'=p_key;
 END IF;
END $$;


ALTER FUNCTION bonafides.resolve_portable_purge_item(p_id uuid, p_holder uuid, p_fence bigint, p_kind text, p_key text, p_outcome text) OWNER TO bonafides_migrator;

-- Name: saved_opportunities saved_opportunities_owner_id_posting_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.saved_opportunities
    ADD CONSTRAINT saved_opportunities_owner_id_posting_id_fkey FOREIGN KEY (owner_id, posting_id) REFERENCES bonafides.job_postings(owner_id, id) ON DELETE RESTRICT;

-- Name: saved_opportunities saved_opportunities_owner_id_posting_id_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.saved_opportunities
    ADD CONSTRAINT saved_opportunities_owner_id_posting_id_key UNIQUE (owner_id, posting_id);

-- Name: saved_opportunities saved_opportunities_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.saved_opportunities
    ADD CONSTRAINT saved_opportunities_pkey PRIMARY KEY (owner_id, id);

-- Name: saved_opportunities; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.saved_opportunities (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    posting_id uuid NOT NULL,
    disposition text DEFAULT 'saved'::text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT saved_opportunities_disposition_check CHECK ((disposition = ANY (ARRAY['saved'::text, 'dismissed'::text, 'archived'::text, 'unmarked'::text]))),
    CONSTRAINT saved_opportunities_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.saved_opportunities OWNER TO bonafides_migrator;

-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);

-- Name: schema_migrations; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.schema_migrations (
    version integer NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    source_sha256 text NOT NULL,
    CONSTRAINT schema_migrations_source_sha256_check CHECK ((source_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.schema_migrations OWNER TO bonafides_migrator;

-- Name: sessions sessions_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.sessions
    ADD CONSTRAINT sessions_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (owner_id, id);

-- Name: sessions sessions_token_sha256_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.sessions
    ADD CONSTRAINT sessions_token_sha256_key UNIQUE (token_sha256);

-- Name: sessions; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.sessions (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_sha256 text NOT NULL,
    csrf_sha256 text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    auth_epoch bigint DEFAULT 1 NOT NULL,
    revoked_at timestamp with time zone,
    authenticated_at timestamp with time zone DEFAULT now() NOT NULL,
    csrf_token text DEFAULT ''::text NOT NULL,
    CONSTRAINT sessions_auth_epoch_check CHECK ((auth_epoch > 0)),
    CONSTRAINT sessions_check CHECK ((expires_at > created_at)),
    CONSTRAINT sessions_token_sha256_check CHECK ((token_sha256 ~ '^[0-9a-f]{64}$'::text))
);


ALTER TABLE bonafides.sessions OWNER TO bonafides_migrator;

-- Name: sessions_expiry_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX sessions_expiry_idx ON bonafides.sessions USING btree (expires_at);

-- Name: settle_installation_control(uuid, uuid, bigint, jsonb); Type: FUNCTION; Schema: bonafides; Owner: bonafides_migrator

CREATE FUNCTION bonafides.settle_installation_control(p_id uuid, p_holder uuid, p_fence bigint, p_result jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'bonafides'
    AS $$
DECLARE c bonafides.installation_controls%ROWTYPE;
BEGIN
 SELECT * INTO STRICT c FROM bonafides.assert_installation_control(p_id,p_holder,p_fence);
 IF c.phase<>'quiescent' OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
 OR octet_length(p_result::text)>16384
 OR EXISTS(SELECT 1 FROM bonafides.installation_activities)
 OR EXISTS(SELECT 1 FROM bonafides.provider_executions)
 OR (c.purpose IN ('privacy_delete','portable_import') AND NOT EXISTS(
  SELECT 1 FROM bonafides.portable_purges WHERE control_id=c.id AND completed_at IS NOT NULL)) THEN
  RAISE EXCEPTION 'installation settlement refused' USING ERRCODE='55000';
 END IF;
 UPDATE bonafides.installation_controls SET phase='settled',settled_at=clock_timestamp(),result=p_result WHERE id=c.id;
END $$;


ALTER FUNCTION bonafides.settle_installation_control(p_id uuid, p_holder uuid, p_fence bigint, p_result jsonb) OWNER TO bonafides_migrator;

-- Name: snapshot_claim_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX snapshot_claim_idx ON bonafides.profile_revision_claims USING btree (owner_id, claim_revision_id);

-- Name: source_configs source_configs_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_configs
    ADD CONSTRAINT source_configs_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: source_configs source_configs_owner_id_source_kind_board_token_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_configs
    ADD CONSTRAINT source_configs_owner_id_source_kind_board_token_key UNIQUE (owner_id, source_kind, board_token);

-- Name: source_configs source_configs_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_configs
    ADD CONSTRAINT source_configs_pkey PRIMARY KEY (owner_id, id);

-- Name: source_configs source_current_review_fk; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_configs
    ADD CONSTRAINT source_current_review_fk FOREIGN KEY (owner_id, id, policy_version, current_policy_review_id) REFERENCES bonafides.source_policy_reviews(owner_id, source_config_id, policy_version, id) DEFERRABLE INITIALLY DEFERRED;

-- Name: source_configs source_identity_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER source_identity_guard BEFORE UPDATE ON bonafides.source_configs FOR EACH ROW EXECUTE FUNCTION bonafides.guard_source_identity();

-- Name: source_configs source_review_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE CONSTRAINT TRIGGER source_review_guard AFTER INSERT OR UPDATE ON bonafides.source_configs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bonafides.check_source_review();

-- Name: source_configs; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.source_configs (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_kind text NOT NULL,
    board_token text,
    enabled boolean DEFAULT false NOT NULL,
    terms_url text,
    terms_reviewed_at timestamp with time zone,
    terms_revision text,
    allowed_host text,
    request_limit_per_minute integer DEFAULT 10 NOT NULL,
    last_success_at timestamp with time zone,
    last_error_code text,
    next_allowed_at timestamp with time zone,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company text DEFAULT 'Unspecified employer'::text NOT NULL,
    policy_version bigint DEFAULT 1 NOT NULL,
    current_policy_review_id uuid,
    rate_limit_blocked boolean DEFAULT false NOT NULL,
    rate_limit_retry_after_seconds text,
    removed_at timestamp with time zone,
    CONSTRAINT source_board_shape CHECK ((((source_kind = 'manual'::text) AND (board_token IS NULL)) OR ((source_kind = 'greenhouse'::text) AND (board_token IS NOT NULL) AND (board_token ~ '^[A-Za-z0-9_-]{1,120}$'::text)))),
    CONSTRAINT source_configs_check CHECK (((NOT enabled) OR (source_kind = 'manual'::text) OR ((terms_reviewed_at IS NOT NULL) AND (terms_url IS NOT NULL) AND (board_token IS NOT NULL) AND (allowed_host = 'boards-api.greenhouse.io'::text)))),
    CONSTRAINT source_configs_company_check CHECK (((length(company) >= 1) AND (length(company) <= 500))),
    CONSTRAINT source_configs_policy_version_check CHECK ((policy_version > 0)),
    CONSTRAINT source_configs_rate_limit_retry_after_seconds_check CHECK ((((length(rate_limit_retry_after_seconds) >= 1) AND (length(rate_limit_retry_after_seconds) <= 4096)) AND (rate_limit_retry_after_seconds ~ '^[0-9]+$'::text))),
    CONSTRAINT source_configs_request_limit_per_minute_check CHECK (((request_limit_per_minute >= 1) AND (request_limit_per_minute <= 60))),
    CONSTRAINT source_configs_source_kind_check CHECK ((source_kind = ANY (ARRAY['manual'::text, 'greenhouse'::text]))),
    CONSTRAINT source_configs_version_check CHECK ((version > 0)),
    CONSTRAINT source_enabled_review CHECK (((source_kind <> 'greenhouse'::text) OR (NOT enabled) OR ((current_policy_review_id IS NOT NULL) AND (terms_reviewed_at IS NOT NULL) AND (terms_url IS NOT NULL) AND (terms_revision IS NOT NULL) AND (allowed_host = 'boards-api.greenhouse.io'::text)))),
    CONSTRAINT source_retired_shape CHECK (((removed_at IS NULL) OR ((source_kind = 'greenhouse'::text) AND (NOT enabled))))
);


ALTER TABLE bonafides.source_configs OWNER TO bonafides_migrator;

-- Name: source_policy_reviews source_policy_reviews_acknowledged_by_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_acknowledged_by_owner_id_fkey FOREIGN KEY (acknowledged_by_owner_id) REFERENCES bonafides.owners(id) ON DELETE RESTRICT;

-- Name: source_policy_reviews source_policy_reviews_owner_id_source_config_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_owner_id_source_config_id_fkey FOREIGN KEY (owner_id, source_config_id) REFERENCES bonafides.source_configs(owner_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

-- Name: source_policy_reviews source_policy_reviews_owner_id_source_config_id_policy_ver_key1; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_owner_id_source_config_id_policy_ver_key1 UNIQUE (owner_id, source_config_id, policy_version, id);

-- Name: source_policy_reviews source_policy_reviews_owner_id_source_config_id_policy_ver_key2; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_owner_id_source_config_id_policy_ver_key2 UNIQUE (owner_id, source_config_id, policy_version, id, policy_sha256);

-- Name: source_policy_reviews source_policy_reviews_owner_id_source_config_id_policy_vers_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_owner_id_source_config_id_policy_vers_key UNIQUE (owner_id, source_config_id, policy_version);

-- Name: source_policy_reviews source_policy_reviews_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.source_policy_reviews
    ADD CONSTRAINT source_policy_reviews_pkey PRIMARY KEY (owner_id, id);

-- Name: source_policy_reviews source_review_immutable; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER source_review_immutable BEFORE UPDATE ON bonafides.source_policy_reviews FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: source_policy_reviews; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.source_policy_reviews (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_config_id uuid NOT NULL,
    policy_version bigint NOT NULL,
    board_token text NOT NULL,
    company text NOT NULL,
    terms_url text NOT NULL,
    terms_revision text NOT NULL,
    allowed_host text NOT NULL,
    request_limit_per_minute integer NOT NULL,
    policy_sha256 text NOT NULL,
    acknowledged_by_owner_id uuid NOT NULL,
    reviewed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT source_policy_reviews_allowed_host_check CHECK ((allowed_host = 'boards-api.greenhouse.io'::text)),
    CONSTRAINT source_policy_reviews_board_token_check CHECK ((board_token ~ '^[A-Za-z0-9_-]{1,120}$'::text)),
    CONSTRAINT source_policy_reviews_check CHECK ((acknowledged_by_owner_id = owner_id)),
    CONSTRAINT source_policy_reviews_company_check CHECK (((length(company) >= 1) AND (length(company) <= 500))),
    CONSTRAINT source_policy_reviews_policy_sha256_check CHECK ((policy_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT source_policy_reviews_policy_version_check CHECK ((policy_version > 0)),
    CONSTRAINT source_policy_reviews_request_limit_per_minute_check CHECK (((request_limit_per_minute >= 1) AND (request_limit_per_minute <= 60))),
    CONSTRAINT source_policy_reviews_terms_revision_check CHECK (((length(terms_revision) >= 1) AND (length(terms_revision) <= 500))),
    CONSTRAINT source_policy_reviews_terms_url_check CHECK (((length(terms_url) >= 1) AND (length(terms_url) <= 2048)))
);


ALTER TABLE bonafides.source_policy_reviews OWNER TO bonafides_migrator;

-- Name: stage_events_app_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX stage_events_app_idx ON bonafides.application_stage_events USING btree (owner_id, application_id, effective_at, id);

-- Name: steps_workflow_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX steps_workflow_idx ON bonafides.workflow_steps USING btree (owner_id, workflow_run_id, created_at, id);

-- Name: tracking_appointments_parent_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX tracking_appointments_parent_idx ON bonafides.appointments USING btree (owner_id, application_id, starts_at, id);

-- Name: tracking_attachments_parent_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX tracking_attachments_parent_idx ON bonafides.application_attachments USING btree (owner_id, application_id, created_at, id);

-- Name: tracking_attention_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX tracking_attention_idx ON bonafides.appointments USING btree (owner_id, starts_at, id) WHERE (status = 'scheduled'::text);

-- Name: tracking_contacts_parent_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX tracking_contacts_parent_idx ON bonafides.application_contacts USING btree (owner_id, application_id, created_at, id);

-- Name: tracking_notes_parent_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX tracking_notes_parent_idx ON bonafides.application_notes USING btree (owner_id, application_id, created_at, id);

-- Name: transfer_manifests transfer_manifests_owner_id_blob_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.transfer_manifests
    ADD CONSTRAINT transfer_manifests_owner_id_blob_id_fkey FOREIGN KEY (owner_id, blob_id) REFERENCES bonafides.blobs(owner_id, id) ON DELETE RESTRICT;

-- Name: transfer_manifests transfer_manifests_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.transfer_manifests
    ADD CONSTRAINT transfer_manifests_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE RESTRICT;

-- Name: transfer_manifests transfer_manifests_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.transfer_manifests
    ADD CONSTRAINT transfer_manifests_pkey PRIMARY KEY (owner_id, id);

-- Name: transfer_manifests; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.transfer_manifests (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    kind text NOT NULL,
    format_version integer DEFAULT 1 NOT NULL,
    blob_id uuid,
    manifest_sha256 text,
    snapshot_at timestamp with time zone,
    preview jsonb,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT transfer_manifests_format_version_check CHECK ((format_version > 0)),
    CONSTRAINT transfer_manifests_kind_check CHECK ((kind = ANY (ARRAY['portable_export'::text, 'import_preview'::text, 'backup'::text, 'restore_validation'::text, 'privacy_delete'::text])))
);


ALTER TABLE bonafides.transfer_manifests OWNER TO bonafides_migrator;

-- Name: transfers_blob_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX transfers_blob_idx ON bonafides.transfer_manifests USING btree (owner_id, blob_id);

-- Name: transfers_workflow_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX transfers_workflow_idx ON bonafides.transfer_manifests USING btree (owner_id, workflow_run_id);

-- Name: worker_heartbeats worker_heartbeats_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.worker_heartbeats
    ADD CONSTRAINT worker_heartbeats_pkey PRIMARY KEY (id);

-- Name: worker_heartbeats; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.worker_heartbeats (
    id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    concurrency integer NOT NULL,
    CONSTRAINT worker_heartbeats_concurrency_check CHECK (((concurrency >= 1) AND (concurrency <= 8)))
);


ALTER TABLE bonafides.worker_heartbeats OWNER TO bonafides_migrator;

-- Name: workflow_attempt_events immutable_update; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER immutable_update BEFORE UPDATE ON bonafides.workflow_attempt_events FOR EACH ROW EXECUTE FUNCTION bonafides.reject_revision_update();

-- Name: workflow_attempt_events workflow_attempt_events_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_attempt_events
    ADD CONSTRAINT workflow_attempt_events_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: workflow_attempt_events workflow_attempt_events_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_attempt_events
    ADD CONSTRAINT workflow_attempt_events_pkey PRIMARY KEY (owner_id, id);

-- Name: workflow_attempt_events; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.workflow_attempt_events (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    attempt integer NOT NULL,
    fence bigint NOT NULL,
    event text NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT workflow_attempt_events_attempt_check CHECK ((attempt >= 0)),
    CONSTRAINT workflow_attempt_events_event_check CHECK ((event = ANY (ARRAY['claimed'::text, 'retry_requested'::text, 'retryable_failure'::text, 'permanent_failure'::text, 'cancelled'::text, 'succeeded'::text, 'interrupted'::text, 'waiting_for_approval'::text, 'approved'::text, 'approval_expired'::text]))),
    CONSTRAINT workflow_attempt_events_fence_check CHECK ((fence >= 0))
);


ALTER TABLE bonafides.workflow_attempt_events OWNER TO bonafides_migrator;

-- Name: workflow_attempt_history_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX workflow_attempt_history_idx ON bonafides.workflow_attempt_events USING btree (owner_id, workflow_run_id, created_at DESC, id DESC);

-- Name: workflow_claim_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX workflow_claim_idx ON bonafides.workflow_runs USING btree (next_attempt_at, created_at, id) WHERE (state = ANY (ARRAY['pending'::bonafides.workflow_state, 'retryable_failure'::bonafides.workflow_state]));

-- Name: workflow_expired_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX workflow_expired_idx ON bonafides.workflow_runs USING btree (lease_expires_at) WHERE (state = 'running'::bonafides.workflow_state);

-- Name: workflow_owner_list_idx; Type: INDEX; Schema: bonafides; Owner: bonafides_migrator

CREATE INDEX workflow_owner_list_idx ON bonafides.workflow_runs USING btree (owner_id, created_at, id);

-- Name: workflow_runs source_workflow_pin_guard; Type: TRIGGER; Schema: bonafides; Owner: bonafides_migrator

CREATE TRIGGER source_workflow_pin_guard BEFORE UPDATE ON bonafides.workflow_runs FOR EACH ROW EXECUTE FUNCTION bonafides.guard_source_workflow_pin();

-- Name: workflow_runs workflow_runs_owner_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES bonafides.owners(id) ON DELETE CASCADE;

-- Name: workflow_runs workflow_runs_owner_id_kind_dedupe_key_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_owner_id_kind_dedupe_key_key UNIQUE (owner_id, kind, dedupe_key);

-- Name: workflow_runs workflow_runs_owner_id_source_config_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_owner_id_source_config_id_fkey FOREIGN KEY (owner_id, source_config_id) REFERENCES bonafides.source_configs(owner_id, id) ON DELETE RESTRICT;

-- Name: workflow_runs workflow_runs_owner_id_source_config_id_source_policy_ver_fkey1; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_owner_id_source_config_id_source_policy_ver_fkey1 FOREIGN KEY (owner_id, source_config_id, source_policy_version, source_policy_review_id, source_policy_sha256) REFERENCES bonafides.source_policy_reviews(owner_id, source_config_id, policy_version, id, policy_sha256) ON DELETE RESTRICT;

-- Name: workflow_runs workflow_runs_owner_id_source_config_id_source_policy_vers_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_owner_id_source_config_id_source_policy_vers_fkey FOREIGN KEY (owner_id, source_config_id, source_policy_version, source_policy_review_id) REFERENCES bonafides.source_policy_reviews(owner_id, source_config_id, policy_version, id) ON DELETE RESTRICT;

-- Name: workflow_runs workflow_runs_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_runs
    ADD CONSTRAINT workflow_runs_pkey PRIMARY KEY (owner_id, id);

-- Name: workflow_runs; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.workflow_runs (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    state bonafides.workflow_state DEFAULT 'pending'::bonafides.workflow_state NOT NULL,
    input jsonb DEFAULT '{}'::jsonb NOT NULL,
    result jsonb,
    dedupe_key text NOT NULL,
    request_sha256 text NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    attempt integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_holder uuid,
    lease_expires_at timestamp with time zone,
    fence bigint DEFAULT 0 NOT NULL,
    cancel_requested_at timestamp with time zone,
    deadline_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    token_budget bigint DEFAULT 0 NOT NULL,
    tokens_used bigint DEFAULT 0 NOT NULL,
    cost_budget_microunits bigint DEFAULT 0 NOT NULL,
    cost_used_microunits bigint DEFAULT 0 NOT NULL,
    budget_currency text DEFAULT 'USD'::text NOT NULL,
    last_error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    maintenance_epoch bigint DEFAULT 0 NOT NULL,
    error_fingerprint text,
    repeated_error_count integer DEFAULT 0 NOT NULL,
    source_config_id uuid,
    source_policy_version bigint,
    source_policy_review_id uuid,
    source_policy_sha256 text,
    model_network_attempts integer DEFAULT 0 NOT NULL,
    last_heartbeat_at timestamp with time zone,
    CONSTRAINT source_workflow_shape CHECK ((((kind <> 'source_refresh'::text) AND (source_config_id IS NULL) AND (source_policy_version IS NULL) AND (source_policy_review_id IS NULL) AND (source_policy_sha256 IS NULL)) OR ((kind = 'source_refresh'::text) AND (source_config_id IS NOT NULL) AND (source_policy_version IS NOT NULL) AND (source_policy_version > 0) AND (source_policy_review_id IS NOT NULL) AND (source_policy_sha256 IS NOT NULL) AND (source_policy_sha256 ~ '^[0-9a-f]{64}$'::text) AND (max_attempts = 3) AND (deadline_at <= (created_at + '00:03:00'::interval)) AND (token_budget = 0) AND (cost_budget_microunits = 0)))),
    CONSTRAINT workflow_runs_budget_currency_check CHECK ((budget_currency ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT workflow_runs_check CHECK (((attempt >= 0) AND (attempt <= max_attempts))),
    CONSTRAINT workflow_runs_check1 CHECK ((tokens_used <= token_budget)),
    CONSTRAINT workflow_runs_check2 CHECK ((cost_used_microunits <= cost_budget_microunits)),
    CONSTRAINT workflow_runs_check3 CHECK (((lease_holder IS NULL) = (lease_expires_at IS NULL))),
    CONSTRAINT workflow_runs_check4 CHECK (((state = 'running'::bonafides.workflow_state) = (lease_holder IS NOT NULL))),
    CONSTRAINT workflow_runs_check5 CHECK (((state = ANY (ARRAY['succeeded'::bonafides.workflow_state, 'cancelled'::bonafides.workflow_state, 'permanent_failure'::bonafides.workflow_state])) = (finished_at IS NOT NULL))),
    CONSTRAINT workflow_runs_check6 CHECK ((deadline_at > created_at)),
    CONSTRAINT workflow_runs_cost_budget_microunits_check CHECK ((cost_budget_microunits >= 0)),
    CONSTRAINT workflow_runs_cost_used_microunits_check CHECK ((cost_used_microunits >= 0)),
    CONSTRAINT workflow_runs_fence_check CHECK ((fence >= 0)),
    CONSTRAINT workflow_runs_input_check CHECK ((jsonb_typeof(input) = 'object'::text)),
    CONSTRAINT workflow_runs_kind_check CHECK ((kind = ANY (ARRAY['profile_import'::text, 'discovery'::text, 'posting_extract'::text, 'matching'::text, 'document_generate'::text, 'pdf_render'::text, 'portable_export'::text, 'portable_import'::text, 'privacy_delete'::text, 'backup'::text, 'restore_validate'::text, 'integration_sync'::text, 'approved_send'::text, 'source_refresh'::text]))),
    CONSTRAINT workflow_runs_maintenance_epoch_check CHECK ((maintenance_epoch >= 0)),
    CONSTRAINT workflow_runs_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 10))),
    CONSTRAINT workflow_runs_model_network_attempts_check CHECK (((model_network_attempts >= 0) AND (model_network_attempts <= 2))),
    CONSTRAINT workflow_runs_repeated_error_count_check CHECK ((repeated_error_count >= 0)),
    CONSTRAINT workflow_runs_request_sha256_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT workflow_runs_token_budget_check CHECK ((token_budget >= 0)),
    CONSTRAINT workflow_runs_tokens_used_check CHECK ((tokens_used >= 0)),
    CONSTRAINT workflow_runs_version_check CHECK ((version > 0))
);


ALTER TABLE bonafides.workflow_runs OWNER TO bonafides_migrator;

-- Name: workflow_state; Type: TYPE; Schema: bonafides; Owner: bonafides_migrator

CREATE TYPE bonafides.workflow_state AS ENUM (
    'pending',
    'running',
    'waiting_for_approval',
    'retryable_failure',
    'permanent_failure',
    'cancelled',
    'succeeded'
);


ALTER TYPE bonafides.workflow_state OWNER TO bonafides_migrator;

-- Name: workflow_steps workflow_steps_owner_id_workflow_run_id_fkey; Type: FK CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_steps
    ADD CONSTRAINT workflow_steps_owner_id_workflow_run_id_fkey FOREIGN KEY (owner_id, workflow_run_id) REFERENCES bonafides.workflow_runs(owner_id, id) ON DELETE CASCADE;

-- Name: workflow_steps workflow_steps_owner_id_workflow_run_id_step_key_attempt_key; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_steps
    ADD CONSTRAINT workflow_steps_owner_id_workflow_run_id_step_key_attempt_key UNIQUE (owner_id, workflow_run_id, step_key, attempt);

-- Name: workflow_steps workflow_steps_pkey; Type: CONSTRAINT; Schema: bonafides; Owner: bonafides_migrator

ALTER TABLE ONLY bonafides.workflow_steps
    ADD CONSTRAINT workflow_steps_pkey PRIMARY KEY (owner_id, id);

-- Name: workflow_steps; Type: TABLE; Schema: bonafides; Owner: bonafides_migrator

CREATE TABLE bonafides.workflow_steps (
    owner_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_run_id uuid NOT NULL,
    step_key text NOT NULL,
    attempt integer NOT NULL,
    fence bigint NOT NULL,
    state text NOT NULL,
    request_sha256 text,
    provider_operation_id text,
    delivery_state text DEFAULT 'not_attempted'::text NOT NULL,
    checkpoint jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT workflow_steps_attempt_check CHECK ((attempt > 0)),
    CONSTRAINT workflow_steps_delivery_state_check CHECK ((delivery_state = ANY (ARRAY['not_attempted'::text, 'known_failed'::text, 'confirmed'::text, 'unknown'::text]))),
    CONSTRAINT workflow_steps_fence_check CHECK ((fence > 0)),
    CONSTRAINT workflow_steps_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'waiting_for_approval'::text])))
);


ALTER TABLE bonafides.workflow_steps OWNER TO bonafides_migrator;

-- Fact: runtime database TEMPORARY true
-- Fact: bonafides.action_approvals rows 0
-- Fact: bonafides.application_attachments rows 0
-- Fact: bonafides.application_contacts rows 0
-- Fact: bonafides.application_notes rows 0
-- Fact: bonafides.application_stage_events rows 0
-- Fact: bonafides.applications rows 0
-- Fact: bonafides.appointments rows 0
-- Fact: bonafides.approval_audit_events rows 0
-- Fact: bonafides.audit_events rows 0
-- Fact: bonafides.auth_challenges rows 0
-- Fact: bonafides.auth_throttles rows 0
-- Fact: bonafides.backup_daily_runs rows 0
-- Fact: bonafides.blobs rows 0
-- Fact: bonafides.claim_evidence_links rows 0
-- Fact: bonafides.claim_revisions rows 0
-- Fact: bonafides.credential_key_usage rows 0
-- Fact: bonafides.document_artifacts rows 0
-- Fact: bonafides.document_claim_links rows 0
-- Fact: bonafides.document_drafts rows 0
-- Fact: bonafides.document_regeneration_proposals rows 0
-- Fact: bonafides.document_reviews rows 0
-- Fact: bonafides.document_revisions rows 0
-- Fact: bonafides.encrypted_credentials rows 0
-- Fact: bonafides.erased_request_keys rows 0
-- Fact: bonafides.event_metadata rows 0
-- Fact: bonafides.event_receipts rows 0
-- Fact: bonafides.evidence_classification_changes rows 0
-- Fact: bonafides.evidence_records rows 0
-- Fact: bonafides.evidence_revisions rows 0
-- Fact: bonafides.external_accounts rows 0
-- Fact: bonafides.hosted_egress_consents rows 0
-- Fact: bonafides.idempotency_records rows 0
-- Fact: bonafides.import_batches rows 0
-- Fact: bonafides.installation_activities rows 0
-- Fact: bonafides.installation_backups rows 0
-- Fact: bonafides.installation_controls rows 0
-- Fact: bonafides.installation_state rows 1
-- Fact: bonafides.job_postings rows 0
-- Fact: bonafides.job_source_observations rows 0
-- Fact: bonafides.local_model_reservations rows 1
-- Fact: bonafides.local_model_route_blocks rows 0
-- Fact: bonafides.maintenance_recoveries rows 0
-- Fact: bonafides.match_assessments rows 0
-- Fact: bonafides.model_runs rows 0
-- Fact: bonafides.oauth_transactions rows 0
-- Fact: bonafides.outbox_events rows 0
-- Fact: bonafides.owner_settings rows 0
-- Fact: bonafides.owners rows 0
-- Fact: bonafides.portable_exports rows 0
-- Fact: bonafides.portable_model_history rows 0
-- Fact: bonafides.portable_preview_chunks rows 0
-- Fact: bonafides.portable_previews rows 0
-- Fact: bonafides.portable_purge_items rows 0
-- Fact: bonafides.portable_purges rows 0
-- Fact: bonafides.portable_staged_files rows 0
-- Fact: bonafides.posting_duplicate_decisions rows 0
-- Fact: bonafides.posting_requirement_revisions rows 0
-- Fact: bonafides.professional_claims rows 0
-- Fact: bonafides.profile_revision_claims rows 0
-- Fact: bonafides.profile_revision_evidence rows 0
-- Fact: bonafides.profile_revisions rows 0
-- Fact: bonafides.profiles rows 0
-- Fact: bonafides.provider_executions rows 0
-- Fact: bonafides.saved_opportunities rows 0
-- Fact: bonafides.sessions rows 0
-- Fact: bonafides.source_configs rows 0
-- Fact: bonafides.source_policy_reviews rows 0
-- Fact: bonafides.transfer_manifests rows 0
-- Fact: bonafides.worker_heartbeats rows 0
-- Fact: bonafides.workflow_attempt_events rows 0
-- Fact: bonafides.workflow_runs rows 0
-- Fact: bonafides.workflow_steps rows 0
