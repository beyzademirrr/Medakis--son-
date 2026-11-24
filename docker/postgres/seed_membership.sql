-- MedAkis: medakis_owner -> medakis_seed role membership (FAZ 1.2C). Superuser, explicit admin step.
-- Run ONLY through scripts/db/seed_membership.ps1 -Onayla (psql --single-transaction). setup.ps1 does
-- not run it, so re-running the general setup never changes role membership.
--
-- Purpose: the seed service writes medakis_dev data inside ONE owner transaction and switches to
-- medakis_seed with SET LOCAL ROLE, so the writes run with medakis_seed's narrow privileges and the
-- temporary GRANT is never committed or visible to other sessions.
--
-- Scope (cluster level, affects only these two roles):
--   SET TRUE      medakis_owner may SET ROLE medakis_seed (and back with RESET ROLE).
--   INHERIT FALSE medakis_owner does not gain medakis_seed's privileges implicitly.
--   ADMIN FALSE   medakis_owner cannot grant medakis_seed to anyone or change its membership.
-- No table privilege, no superuser and no new role is created. medakis_seed gains nothing.
-- Undo: REVOKE medakis_seed FROM medakis_owner;
--
-- Atomic: the whole check -> grant -> verify runs as ONE statement (a DO block). Any unexpected
-- existing membership involving a medakis_* role is refused BEFORE the grant; a failed verification
-- raises inside the same statement, so nothing is left half done.

DO $$
DECLARE
    expected int;
    other int;
    rows_after int;
    ok_after int;
BEGIN
    -- Bound mode (seed_membership.ps1 -KonteynerKimligi): the caller set the expected instance in this
    -- same transaction; any other PostgreSQL instance is refused before anything else.
    IF coalesce(current_setting('medakis.beklenen_sistem', true), '') <> ''
       AND (SELECT system_identifier::text FROM pg_control_system()) <> current_setting('medakis.beklenen_sistem', true) THEN
        RAISE EXCEPTION 'target PostgreSQL instance mismatch; nothing changed';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_owner')
       OR NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_seed') THEN
        RAISE EXCEPTION 'medakis_owner/medakis_seed missing: run provision.sql first';
    END IF;

    SELECT count(*) FILTER (WHERE r.rolname = 'medakis_seed' AND u.rolname = 'medakis_owner'
                              AND NOT m.admin_option AND NOT m.inherit_option AND m.set_option),
           count(*) FILTER (WHERE NOT (r.rolname = 'medakis_seed' AND u.rolname = 'medakis_owner'
                              AND NOT m.admin_option AND NOT m.inherit_option AND m.set_option))
      INTO expected, other
      FROM pg_auth_members m
      JOIN pg_roles r ON r.oid = m.roleid
      JOIN pg_roles u ON u.oid = m.member
     WHERE r.rolname LIKE 'medakis\_%' OR u.rolname LIKE 'medakis\_%';

    IF other > 0 THEN
        RAISE EXCEPTION 'unexpected medakis role membership present (% row(s)); nothing changed', other;
    END IF;
    IF expected = 1 THEN
        RAISE NOTICE 'medakis_owner -> medakis_seed (SET only) already present; nothing changed';
        RETURN;
    END IF;

    EXECUTE 'GRANT medakis_seed TO medakis_owner WITH INHERIT FALSE, SET TRUE, ADMIN FALSE';

    SELECT count(*), count(*) FILTER (WHERE NOT m.admin_option AND NOT m.inherit_option AND m.set_option)
      INTO rows_after, ok_after
      FROM pg_auth_members m
      JOIN pg_roles r ON r.oid = m.roleid
      JOIN pg_roles u ON u.oid = m.member
     WHERE r.rolname LIKE 'medakis\_%' OR u.rolname LIKE 'medakis\_%';
    IF rows_after <> 1 OR ok_after <> 1 THEN
        RAISE EXCEPTION 'membership verification failed (rows %, set-only %); rolled back', rows_after, ok_after;
    END IF;
    RAISE NOTICE 'medakis_owner -> medakis_seed (SET only) granted and verified';
END
$$;
