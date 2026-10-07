-- 101 B3: the event 'audit_read' (phase-B spec §9; issues-log D-03). GET /api/audit writes one for
-- every read of the log by the security auditor, so reading the log is itself on record.
-- 001 now lists every event itself (D-13 edited it in place); this file re-creates the same CHECK
-- so a database migrated before that edit ends up identical. Keep both lists equal to
-- corpai/audit/log.py EVENTS (tests/audit checks that).

DO $$
DECLARE
  c record;
BEGIN
  -- the inline CHECK on audit.log.event (its generated name is not part of any contract)
  FOR c IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'audit.log'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%owner_job%'
  LOOP
    EXECUTE format('ALTER TABLE audit.log DROP CONSTRAINT %I', c.conname);
  END LOOP;
END
$$;

ALTER TABLE audit.log ADD CONSTRAINT log_event_check CHECK (event IN (
  'login', 'demo_login', 'policy_decision', 'tool_call', 'retrieval', 'model_call',
  'route_decision', 'proposal_created', 'proposal_approved', 'proposal_rejected',
  'proposal_executed', 'export', 'outage_switch', 'budget_exceeded', 'break_glass',
  'owner_job', 'audit_read', 'metric_signed', 'metric_published', 'hr_screening', 'hr_decision'));
