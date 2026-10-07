-- 010 A4 gateway: outage switches, per-department token budgets, external egress log
-- (architecture §8, design-review 二.2 and 二.9).
--
-- Trust model: every table here is owner-only data (RLS on, no policy, no grants). corpai_app
-- reaches them only through the SECURITY DEFINER functions below, which take the caller's
-- identity from the principal_tx session variables (core.cur_user() / cur_role() / cur_dept()),
-- never from a parameter.

-- ---- outage switches ------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.switches (
  name       text PRIMARY KEY CHECK (name IN ('local_down', 'external_down')),
  "on"       boolean NOT NULL DEFAULT false,
  reason     text,
  updated_by text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ops.switches (name, "on", reason, updated_by)
VALUES ('local_down', false, 'initial', 'svc:migrate'), ('external_down', false, 'initial', 'svc:migrate')
ON CONFLICT (name) DO NOTHING;

ALTER TABLE ops.switches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.switches FROM PUBLIC;

-- Anyone on the request path may read the switches: the gateway checks them on every call and
-- the admin page shows them. They hold no business content.
CREATE OR REPLACE FUNCTION ops.get_switches()
RETURNS TABLE (name text, "on" boolean, reason text, updated_by text, updated_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
  SELECT s.name, s."on", s.reason, s.updated_by, s.updated_at FROM ops.switches s ORDER BY s.name
$$;

-- Only an active platform admin (role AND group, like audit.read_log) may flip a switch.
-- The Python wrapper (corpai.gateway.switches.set_switch) audits every attempt as outage_switch.
CREATE OR REPLACE FUNCTION ops.set_switch(p_name text, p_on boolean, p_reason text)
RETURNS TABLE (name text, "on" boolean, reason text, updated_by text, updated_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
BEGIN
  IF core.cur_role() IS DISTINCT FROM 'platform_admin'
     OR NOT ('role:platform_admin' = ANY (core.cur_groups()))
     OR core.cur_clearance() <= 0 THEN
    RAISE EXCEPTION 'only the platform admin may change outage switches' USING ERRCODE = '42501';
  END IF;
  IF p_name IS NULL OR p_name NOT IN ('local_down', 'external_down') THEN
    RAISE EXCEPTION 'unknown switch' USING ERRCODE = '22023';
  END IF;
  IF p_on IS NULL OR p_reason IS NULL OR length(btrim(p_reason)) = 0 OR length(p_reason) > 500 THEN
    RAISE EXCEPTION 'a switch change needs on/off and a reason (max 500 chars)' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    UPDATE ops.switches s SET "on" = p_on, reason = p_reason, updated_by = core.cur_user(), updated_at = now()
    WHERE s.name = p_name
    RETURNING s.name, s."on", s.reason, s.updated_by, s.updated_at;
END
$$;

-- ---- token budgets ----------------------------------------------------------------------------

-- Daily limit per department; '*' is the fallback for a department without its own row.
CREATE TABLE IF NOT EXISTS ops.budget_defaults (
  dept        text PRIMARY KEY CHECK (dept IN ('*', 'sales', 'eng', 'legal', 'finance', 'hr', 'exec', 'it')),
  token_limit bigint NOT NULL CHECK (token_limit >= 0)
);
INSERT INTO ops.budget_defaults (dept, token_limit) VALUES
  ('*', 2000000), ('sales', 2000000), ('eng', 2000000), ('legal', 2000000), ('finance', 2000000),
  ('hr', 2000000), ('exec', 2000000), ('it', 2000000)
ON CONFLICT (dept) DO NOTHING;

-- One row per department per budget day, created on first use with the default limit.
-- token_limit is copied so an owner can raise or lower one day without touching the defaults.
--
-- The budget day is the wall-clock UTC date, computed HERE (ops.budget_day()), not a parameter:
-- the simulation date (clock.today()) never advances, so keying on it would make the "daily"
-- budget a lifetime cap, and a caller-chosen day would let any request path open a fresh budget
-- (e.g. day = 2099-01-01) and bypass BudgetExceeded.
CREATE TABLE IF NOT EXISTS ops.budgets (
  dept        text NOT NULL,
  day         date NOT NULL,
  tokens_used bigint NOT NULL DEFAULT 0 CHECK (tokens_used >= 0),
  token_limit bigint NOT NULL CHECK (token_limit >= 0),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (dept, day)
);

-- Every reservation made by consume_budget(). settle_budget() takes the reservation id, so a
-- caller can only refund what it actually reserved, once, as the same user (never "refund 10^12").
CREATE TABLE IF NOT EXISTS ops.budget_reservations (
  reservation_id bigserial PRIMARY KEY,
  dept           text NOT NULL,
  day            date NOT NULL,
  user_id        text NOT NULL,
  run_id         text,
  tokens         bigint NOT NULL CHECK (tokens >= 0),
  created_at     timestamptz NOT NULL DEFAULT now(),
  settled_at     timestamptz,
  actual_tokens  bigint CHECK (actual_tokens >= 0),
  FOREIGN KEY (dept, day) REFERENCES ops.budgets (dept, day)
);
CREATE INDEX IF NOT EXISTS budget_reservations_open ON ops.budget_reservations (created_at)
  WHERE settled_at IS NULL;

ALTER TABLE ops.budget_defaults ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.budget_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.budget_defaults, ops.budgets, ops.budget_reservations FROM PUBLIC;
REVOKE ALL ON SEQUENCE ops.budget_reservations_reservation_id_seq FROM PUBLIC;

CREATE OR REPLACE FUNCTION ops.budget_day() RETURNS date
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT (now() AT TIME ZONE 'UTC')::date
$$;

-- The budget belongs to the caller's department: p_dept must equal app.dept, so one department
-- cannot spend (or exhaust) another's budget through this function.
CREATE OR REPLACE FUNCTION ops._budget_caller(p_dept text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = ops, pg_temp AS $$
BEGIN
  IF core.cur_user() IS NULL OR core.cur_dept() IS NULL OR p_dept IS DISTINCT FROM core.cur_dept() THEN
    RAISE EXCEPTION 'budget calls need a principal of the same department' USING ERRCODE = '42501';
  END IF;
END
$$;

-- Reserve p_tokens of today's budget. remaining = tokens left after the reservation (>= 0) and
-- reservation_id identifies it for settle_budget(). When the reservation does not fit, NOTHING is
-- consumed, remaining is negative (limit - used - p_tokens) and reservation_id is NULL: the
-- gateway raises BudgetExceeded. The row update is atomic, so concurrent calls cannot overspend.
CREATE OR REPLACE FUNCTION ops.consume_budget(p_dept text, p_tokens bigint,
                                              OUT remaining bigint, OUT reservation_id bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
  v_day date := ops.budget_day();
BEGIN
  PERFORM ops._budget_caller(p_dept);
  IF p_tokens IS NULL OR p_tokens < 0 THEN
    RAISE EXCEPTION 'invalid budget request' USING ERRCODE = '22023';
  END IF;
  INSERT INTO ops.budgets (dept, day, tokens_used, token_limit)
  VALUES (p_dept, v_day, 0, coalesce((SELECT d.token_limit FROM ops.budget_defaults d WHERE d.dept = p_dept),
                                     (SELECT d.token_limit FROM ops.budget_defaults d WHERE d.dept = '*'), 0))
  ON CONFLICT (dept, day) DO NOTHING;
  UPDATE ops.budgets b SET tokens_used = b.tokens_used + p_tokens, updated_at = now()
  WHERE b.dept = p_dept AND b.day = v_day AND b.tokens_used + p_tokens <= b.token_limit
  RETURNING b.token_limit - b.tokens_used INTO remaining;
  IF FOUND THEN
    INSERT INTO ops.budget_reservations (dept, day, user_id, run_id, tokens)
    VALUES (p_dept, v_day, core.cur_user(), nullif(current_setting('app.run_id', true), ''), p_tokens)
    RETURNING ops.budget_reservations.reservation_id INTO reservation_id;
    RETURN;
  END IF;
  SELECT b.token_limit - b.tokens_used - p_tokens INTO remaining FROM ops.budgets b
  WHERE b.dept = p_dept AND b.day = v_day;
  remaining := least(coalesce(remaining, -1), -1);
  reservation_id := NULL;
END
$$;

-- After the call: replace one reservation with what the backend actually reported. Only the user
-- who made the reservation (same department) may settle it, and only once; the refund is bounded
-- by what was reserved. The actual usage always applies (the tokens were really spent, even
-- beyond the reservation); used never drops below 0.
CREATE OR REPLACE FUNCTION ops.settle_budget(p_reservation_id bigint, p_actual bigint)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
  r ops.budget_reservations;
  v_left bigint;
BEGIN
  IF p_reservation_id IS NULL OR p_actual IS NULL OR p_actual < 0 THEN
    RAISE EXCEPTION 'invalid budget settlement' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO r FROM ops.budget_reservations x WHERE x.reservation_id = p_reservation_id FOR UPDATE;
  IF NOT FOUND OR core.cur_user() IS NULL OR r.user_id IS DISTINCT FROM core.cur_user()
     OR r.dept IS DISTINCT FROM core.cur_dept() THEN
    RAISE EXCEPTION 'not your budget reservation' USING ERRCODE = '42501';
  END IF;
  IF r.settled_at IS NOT NULL THEN
    RAISE EXCEPTION 'budget reservation already settled' USING ERRCODE = '55000';
  END IF;
  UPDATE ops.budget_reservations x SET settled_at = now(), actual_tokens = p_actual
  WHERE x.reservation_id = p_reservation_id;
  UPDATE ops.budgets b SET tokens_used = greatest(0, b.tokens_used - r.tokens + p_actual), updated_at = now()
  WHERE b.dept = r.dept AND b.day = r.day
  RETURNING b.token_limit - b.tokens_used INTO v_left;
  RETURN v_left;
END
$$;

-- ---- external egress log ----------------------------------------------------------------------

-- Every payload sent to the external route, appended BEFORE it is sent. The canary test scans
-- this table; nobody but the owner reads it.
CREATE TABLE IF NOT EXISTS ops.egress_log (
  id             bigserial PRIMARY KEY,
  ts             timestamptz NOT NULL DEFAULT now(),
  destination    text NOT NULL,
  route          text NOT NULL CHECK (route IN ('local', 'external')),
  level          smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  run_id         text,
  user_id        text NOT NULL,
  payload        text NOT NULL,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  -- Defence in depth for rule 3: an L3/L4 payload can never be logged as external egress, and
  -- the gateway does not send what it could not log.
  CONSTRAINT egress_external_max_l2 CHECK (route <> 'external' OR level <= 2)
);
CREATE INDEX IF NOT EXISTS egress_log_run ON ops.egress_log (run_id);

ALTER TABLE ops.egress_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.egress_log FROM PUBLIC;
REVOKE ALL ON SEQUENCE ops.egress_log_id_seq FROM PUBLIC;

-- user_id comes from app.user_id and the hash is computed here, not trusted from the caller.
CREATE OR REPLACE FUNCTION ops.log_egress(p_destination text, p_route text, p_level smallint,
                                          p_run_id text, p_payload text)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
  v_id bigint;
BEGIN
  IF core.cur_user() IS NULL THEN
    RAISE EXCEPTION 'egress logging needs a principal (app.user_id)' USING ERRCODE = '42501';
  END IF;
  IF p_destination IS NULL OR p_route IS NULL OR p_level IS NULL OR p_payload IS NULL THEN
    RAISE EXCEPTION 'invalid egress record' USING ERRCODE = '22023';
  END IF;
  IF p_route = 'external' AND p_level > 2 THEN
    RAISE EXCEPTION 'L3/L4 data may not leave through the external route' USING ERRCODE = '42501';
  END IF;
  INSERT INTO ops.egress_log (destination, route, level, run_id, user_id, payload, payload_sha256)
  VALUES (p_destination, p_route, p_level, p_run_id, core.cur_user(), p_payload,
          encode(sha256(convert_to(p_payload, 'UTF8')), 'hex'))
  RETURNING id INTO v_id;
  RETURN v_id;
END
$$;

REVOKE ALL ON FUNCTION ops.get_switches(), ops.set_switch(text, boolean, text), ops.budget_day(),
  ops._budget_caller(text), ops.consume_budget(text, bigint), ops.settle_budget(bigint, bigint),
  ops.log_egress(text, text, smallint, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.get_switches(), ops.set_switch(text, boolean, text), ops.budget_day(),
  ops.consume_budget(text, bigint), ops.settle_budget(bigint, bigint),
  ops.log_egress(text, text, smallint, text, text) TO corpai_app;
