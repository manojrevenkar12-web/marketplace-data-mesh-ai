-- Service records: who may call the API, access requests and their decisions, feedback on AI answers, and an
-- append-only audit of everything that changes access or answers a question.

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash   text PRIMARY KEY,
    user_id    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

CREATE TABLE IF NOT EXISTS access_requests (
    id            bigserial PRIMARY KEY,
    user_id       text NOT NULL,
    principal     text NOT NULL,
    product       text NOT NULL,
    sensitivities text[] NOT NULL,
    reason        text NOT NULL CHECK (length(reason) >= 10),
    days          int NOT NULL CHECK (days BETWEEN 1 AND 365),
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    owner         text NOT NULL,
    decided_by    text,
    decided_at    timestamptz,
    expires_on    date,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_requests_owner ON access_requests (owner, status);
CREATE UNIQUE INDEX IF NOT EXISTS access_requests_one_pending ON access_requests (user_id, product) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS feedback (
    id         bigserial PRIMARY KEY,
    user_id    text NOT NULL,
    feature    text NOT NULL CHECK (feature IN ('ask', 'triage', 'review')),
    ref        text NOT NULL,
    helpful    boolean NOT NULL,
    comment    text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    user_id    text NOT NULL,
    action     text NOT NULL,
    object     text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE OR REPLACE FUNCTION audit_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit is append-only';
END $$;

DROP TRIGGER IF EXISTS audit_no_change ON audit;
CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION audit_is_append_only();

-- a decided request is final: a new request is needed to change it
CREATE OR REPLACE FUNCTION decided_requests_are_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'access request % is already %', OLD.id, OLD.status;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS access_requests_final ON access_requests;
CREATE TRIGGER access_requests_final BEFORE UPDATE ON access_requests FOR EACH ROW EXECUTE FUNCTION decided_requests_are_final();
