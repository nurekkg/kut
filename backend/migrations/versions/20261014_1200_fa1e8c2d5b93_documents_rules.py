"""документы: определители, триггеры, RLS и права S5a (ADR-015 часть 1 раздел 3, часть 2 разделы 5-8)

Revision ID: fa1e8c2d5b93
Revises: 2633d20e6f23
Create Date: 2026-10-14 12:00:00.000000

- Видимость документа - одна функция app.document_allowed(id): политика SELECT внешней роли и выдача
  ссылки на файл вызывают её же. Внутренние роли - политика по объектам и юрлицам с правом
  documents:read; внешние - контрагент, вид из списка роли, утверждённая версия, без архива.
- Номер документа: app.next_document_number - счётчик с блокировкой строки, без дыр (откат возвращает
  значение). Номер версии: app.document_next_version блокирует документ (FOR UPDATE).
- Статус версии меняет только app.document_version_transition (владелец kut_auth). В S5a её вызывают
  системные сессии (сид, тесты); маршруты согласования - S6a, там появляются пользовательские переходы.
- Журнал статусов и версии: APPEND_ONLY и неизменяемость; приложение UPDATE версии не получает.
- Архив документа - app.document_archive (право documents:write:archive); DELETE не выдаётся никому.
- Денежный журнал (F1): строки document_amount в audit_log видны только с documents:read:financial.
- Счётчик номеров не виден приложению: политика «никому» (таблица с колонкой юрлица не в allowlist).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "fa1e8c2d5b93"
down_revision: str | Sequence[str] | None = "2633d20e6f23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
LEGAL_ENTITIES = "((SELECT app.legal_entity_ids())::uuid[])"
CAN_READ_DOCS = "(SELECT app.has_permission('documents', 'read'))"
CAN_READ_FINANCIAL = "(SELECT app.has_permission('documents', 'read', 'financial'))"

CUSTOMER_KINDS = (
    "'commissioning_act', 'contract_customer', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
)
SUBCONTRACTOR_KINDS = "'contract_subcontract', 'hidden_works_act', 'ird', 'ks2', 'ks3', 'ks6'"
SUPPLIER_KINDS = (
    "'contract_supply', 'invoice_esf', 'material_request', 'waybill_m15', 'waybill_torg12'"
)

FUNCTIONS: tuple[str, ...] = (
    """
CREATE FUNCTION app.user_role_code() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT r.code FROM public."user" u JOIN public.role r ON r.id = u.role_id
    WHERE u.id = app.user_id()
$$
""",
    f"""
CREATE FUNCTION app.document_allowed(p_document uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = p_document;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF app.is_internal() THEN
        RETURN app.has_permission('documents', 'read') AND (
            d.project_id = ANY(app.project_ids())
            OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids())));
    END IF;
    IF NOT app.has_permission('documents', 'read') OR d.archived_at IS NOT NULL
       OR d.counterparty_id IS NULL OR d.counterparty_id IS DISTINCT FROM app.counterparty_id()
       OR (d.project_id IS NOT NULL AND NOT d.project_id = ANY(app.project_ids())) THEN
        RETURN false;
    END IF;
    IF NOT (
        (app.user_role_code() = 'customer' AND d.kind IN ({CUSTOMER_KINDS}))
        OR (app.user_role_code() = 'subcontractor' AND d.kind IN ({SUBCONTRACTOR_KINDS}))
        OR (app.user_role_code() = 'supplier' AND d.kind IN ({SUPPLIER_KINDS}))) THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = d.current_version_id AND v.status IN ('approved', 'signed'));
END $$
""",
    """
CREATE FUNCTION app.document_scope_writable(p_project uuid, p_legal_entity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT app.is_internal() AND app.has_permission('documents', 'write') AND (
        p_project = ANY(app.project_ids())
        OR (p_project IS NULL AND p_legal_entity = ANY(app.legal_entity_ids())))
$$
""",
    """
CREATE FUNCTION app.document_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((
        SELECT app.document_scope_writable(d.project_id, d.legal_entity_id)
        FROM public.document d WHERE d.id = p_document), false)
$$
""",
    """
CREATE FUNCTION app.document_amount_writable(p_document uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT app.document_writable(p_document) AND app.has_permission('documents', 'read', 'financial')
$$
""",
    """
CREATE FUNCTION app.document_version_storage_key(p_version uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    d public.document;
BEGIN
    SELECT * INTO v FROM public.document_version WHERE id = p_version;
    IF NOT FOUND OR NOT app.document_allowed(v.document_id) THEN
        RETURN NULL;
    END IF;
    IF NOT app.is_internal() AND v.status NOT IN ('approved', 'signed') THEN
        RETURN NULL;
    END IF;
    SELECT * INTO d FROM public.document WHERE id = v.document_id;
    IF app.is_internal() AND d.financial AND NOT app.has_permission('documents', 'read', 'financial') THEN
        RETURN NULL;
    END IF;
    RETURN v.s3_key;
END $$
""",
    """
CREATE FUNCTION app.document_archive(p_document uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF app.user_id() IS NULL OR NOT app.is_internal()
       OR NOT app.has_permission('documents', 'write', 'archive') THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    PERFORM 1 FROM public.document d
    WHERE d.id = p_document AND (
        d.project_id = ANY(app.project_ids())
        OR (d.project_id IS NULL AND d.legal_entity_id = ANY(app.legal_entity_ids())));
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    UPDATE public.document
    SET archived_at = now(), updated_at = now(), updated_by = app.user_id()
    WHERE id = p_document AND archived_at IS NULL;
END $$
""",
    """
CREATE FUNCTION app.next_document_number(p_legal_entity uuid, p_kind text, p_year integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_no integer;
BEGIN
    IF app.user_id() IS NOT NULL AND (
        NOT app.is_internal() OR NOT app.has_permission('documents', 'write')
        OR NOT p_legal_entity = ANY(app.legal_entity_ids())) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    INSERT INTO public.document_number_counter AS c (legal_entity_id, kind, year, last_no)
    VALUES (p_legal_entity, p_kind, p_year, 1)
    ON CONFLICT (legal_entity_id, kind, year) DO UPDATE SET last_no = c.last_no + 1
    RETURNING c.last_no INTO v_no;
    RETURN v_no;
END $$
""",
    """
CREATE FUNCTION app.document_next_version(p_document uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_no integer;
BEGIN
    IF app.user_id() IS NOT NULL AND NOT app.document_writable(p_document) THEN
        RAISE EXCEPTION 'document.forbidden';
    END IF;
    PERFORM 1 FROM public.document WHERE id = p_document FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    SELECT COALESCE(MAX(version_no), 0) + 1 INTO v_no
    FROM public.document_version WHERE document_id = p_document;
    RETURN v_no;
END $$
""",
    """
CREATE FUNCTION app.document_version_transition(p_version uuid, p_to text, p_comment text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v public.document_version;
    r public.document_version;
BEGIN
    IF app.user_id() IS NOT NULL THEN
        RAISE EXCEPTION 'document.transition_forbidden';
    END IF;
    SELECT * INTO v FROM public.document_version WHERE id = p_version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF NOT (
        (v.status = 'draft' AND p_to = 'on_approval')
        OR (v.status = 'on_approval' AND p_to IN ('approved', 'draft'))
        OR (v.status = 'approved' AND p_to IN ('signed', 'archived'))) THEN
        RAISE EXCEPTION 'document.transition_invalid';
    END IF;
    IF p_to = 'draft' AND (p_comment IS NULL OR btrim(p_comment) = '') THEN
        RAISE EXCEPTION 'document.comment_required';
    END IF;
    UPDATE public.document_version SET status = p_to, updated_at = now() WHERE id = p_version;
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (p_version, v.project_id, v.legal_entity_id, v.status, p_to,
            NULLIF(btrim(p_comment), ''), NULL);
    IF p_to = 'approved' THEN
        FOR r IN SELECT * FROM public.document_version
                 WHERE document_id = v.document_id AND status IN ('approved', 'signed')
                   AND id <> p_version LOOP
            UPDATE public.document_version SET status = 'archived', updated_at = now()
            WHERE id = r.id;
            INSERT INTO public.document_status_event
                (document_version_id, project_id, legal_entity_id, from_status, to_status,
                 comment, actor_id)
            VALUES (r.id, r.project_id, r.legal_entity_id, r.status, 'archived', NULL, NULL);
        END LOOP;
        UPDATE public.document SET current_version_id = p_version, updated_at = now()
        WHERE id = v.document_id;
    END IF;
END $$
""",
    """
CREATE FUNCTION app.document_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.contract_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.contract c
        WHERE c.id = NEW.contract_id AND c.legal_entity_id = NEW.legal_entity_id) THEN
        RAISE EXCEPTION 'document.contract_invalid';
    END IF;
    IF NEW.current_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.document_version v
        WHERE v.id = NEW.current_version_id AND v.document_id = NEW.id) THEN
        RAISE EXCEPTION 'document.version_invalid';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.document_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    IF NEW.status <> 'draft' OR NEW.parent_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'document.version_status_invalid';
    END IF;
    NEW.project_id := d.project_id;
    NEW.legal_entity_id := d.legal_entity_id;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.document_version_created() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.document_status_event
        (document_version_id, project_id, legal_entity_id, from_status, to_status, comment, actor_id)
    VALUES (NEW.id, NEW.project_id, NEW.legal_entity_id, NULL, 'draft', NULL, app.user_id());
    RETURN NULL;
END $$
""",
    """
CREATE FUNCTION app.document_amount_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'document.not_found';
        END IF;
        IF NOT d.financial THEN
            RAISE EXCEPTION 'document.amount_not_financial';
        END IF;
        NEW.project_id := d.project_id;
        NEW.legal_entity_id := d.legal_entity_id;
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.document_link_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    d public.document;
BEGIN
    SELECT * INTO d FROM public.document WHERE id = NEW.document_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document.not_found';
    END IF;
    NEW.project_id := d.project_id;
    NEW.legal_entity_id := d.legal_entity_id;
    IF NEW.project_node_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.project_node n
        WHERE n.id = NEW.project_node_id AND n.project_id IS NOT DISTINCT FROM d.project_id) THEN
        RAISE EXCEPTION 'document.link_invalid';
    END IF;
    IF NEW.work_actual_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.work_actual a
        WHERE a.id = NEW.work_actual_id AND a.project_id IS NOT DISTINCT FROM d.project_id) THEN
        RAISE EXCEPTION 'document.link_invalid';
    END IF;
    IF NEW.equipment_unit_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.equipment_unit u
        WHERE u.id = NEW.equipment_unit_id AND u.legal_entity_id = d.legal_entity_id) THEN
        RAISE EXCEPTION 'document.link_invalid';
    END IF;
    RETURN NEW;
END $$
""",
    """
CREATE FUNCTION app.document_event_forbid() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'document.event_immutable' USING ERRCODE = 'check_violation';
END $$
""",
)

TRIGGERS: tuple[str, ...] = (
    "CREATE TRIGGER document_guard BEFORE INSERT OR UPDATE ON document"
    " FOR EACH ROW EXECUTE FUNCTION app.document_guard()",
    "CREATE TRIGGER document_version_guard BEFORE INSERT ON document_version"
    " FOR EACH ROW EXECUTE FUNCTION app.document_version_guard()",
    "CREATE TRIGGER document_version_created AFTER INSERT ON document_version"
    " FOR EACH ROW EXECUTE FUNCTION app.document_version_created()",
    "CREATE TRIGGER document_amount_guard BEFORE INSERT OR UPDATE ON document_amount"
    " FOR EACH ROW EXECUTE FUNCTION app.document_amount_guard()",
    "CREATE TRIGGER document_link_guard BEFORE INSERT ON document_link"
    " FOR EACH ROW EXECUTE FUNCTION app.document_link_guard()",
    "CREATE TRIGGER document_status_event_forbid BEFORE UPDATE OR DELETE ON document_status_event"
    " FOR EACH ROW EXECUTE FUNCTION app.document_event_forbid()",
)

# Выполняющие функции: вызывает приложение (политики и сервис). Служебные не выдаются.
CALLABLE: tuple[tuple[str, str], ...] = (
    ("document_allowed(uuid)", "kut_app, kut_portal"),
    ("document_scope_writable(uuid, uuid)", "kut_app"),
    ("document_writable(uuid)", "kut_app"),
    ("document_amount_writable(uuid)", "kut_app"),
    ("document_version_storage_key(uuid)", "kut_app, kut_portal"),
    ("document_archive(uuid)", "kut_app"),
    ("next_document_number(uuid, text, integer)", "kut_app, kut_worker"),
    ("document_next_version(uuid)", "kut_app"),
    ("document_version_transition(uuid, text, text)", "kut_app, kut_worker"),
)
TRIGGER_FUNCTIONS: tuple[str, ...] = (
    "document_guard()",
    "document_version_guard()",
    "document_version_created()",
    "document_amount_guard()",
    "document_link_guard()",
    "document_event_forbid()",
)

INTERNAL_SCOPE = (
    f"{INTERNAL} AND {CAN_READ_DOCS} AND (project_id = ANY {PROJECTS}"
    f" OR (project_id IS NULL AND legal_entity_id = ANY {LEGAL_ENTITIES}))"
)


def upgrade() -> None:
    # Функции создаёт владелец kut_auth (SECURITY DEFINER); триггеры - владелец таблицы (kut_owner).
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in FUNCTIONS:
        op.execute(sql)
    for signature in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        # Владелец таблицы создаёт триггер и должен иметь EXECUTE на его функцию.
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO kut_owner")
    for signature, logins in CALLABLE:
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO {logins}")
    op.execute("REVOKE ALL ON FUNCTION app.user_role_code() FROM PUBLIC")
    op.execute("RESET ROLE")
    # Гранты выдаёт владелец таблиц (kut_owner), не kut_auth.
    op.execute(
        "GRANT SELECT ON project, project_node, work_actual, equipment_unit, contract, counterparty,"
        ' "user", role, work TO kut_auth'
    )
    for sql in TRIGGERS:
        op.execute(sql)

    # document: видимость внутренним по объекту или юрлицу с правом чтения; внешним - через функцию.
    op.execute(
        "CREATE POLICY document_select_app ON document FOR SELECT TO kut_app USING ("
        f"{INTERNAL_SCOPE})"
    )
    op.execute(
        "CREATE POLICY document_select_portal ON document FOR SELECT TO kut_portal"
        " USING (app.document_allowed(id))"
    )
    op.execute(
        "CREATE POLICY document_insert_app ON document FOR INSERT TO kut_app"
        " WITH CHECK (app.document_scope_writable(project_id, legal_entity_id))"
    )
    op.execute(
        "CREATE POLICY document_update_app ON document FOR UPDATE TO kut_app"
        " USING (app.document_scope_writable(project_id, legal_entity_id))"
        " WITH CHECK (app.document_scope_writable(project_id, legal_entity_id))"
    )

    op.execute(
        "CREATE POLICY document_version_select_app ON document_version FOR SELECT TO kut_app"
        " USING (EXISTS (SELECT 1 FROM public.document d WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_version_select_portal ON document_version FOR SELECT TO kut_portal"
        " USING (status IN ('approved', 'signed') AND app.document_allowed(document_id))"
    )
    op.execute(
        "CREATE POLICY document_version_insert_app ON document_version FOR INSERT TO kut_app"
        " WITH CHECK (app.document_writable(document_id))"
    )

    op.execute(
        "CREATE POLICY document_status_event_select_app ON document_status_event"
        " FOR SELECT TO kut_app USING (EXISTS (SELECT 1 FROM public.document_version v"
        " WHERE v.id = document_version_id))"
    )

    op.execute(
        "CREATE POLICY document_link_select_app ON document_link FOR SELECT TO kut_app"
        " USING (EXISTS (SELECT 1 FROM public.document d WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_link_insert_app ON document_link FOR INSERT TO kut_app"
        " WITH CHECK (app.document_writable(document_id))"
    )

    op.execute(
        "CREATE POLICY document_amount_select_app ON document_amount FOR SELECT TO kut_app"
        f" USING ({CAN_READ_FINANCIAL} AND EXISTS (SELECT 1 FROM public.document d"
        " WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_select_portal ON document_amount FOR SELECT TO kut_portal"
        " USING (EXISTS (SELECT 1 FROM public.document d WHERE d.id = document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_insert_app ON document_amount FOR INSERT TO kut_app"
        " WITH CHECK (app.document_amount_writable(document_id))"
    )
    op.execute(
        "CREATE POLICY document_amount_update_app ON document_amount FOR UPDATE TO kut_app"
        " USING (app.document_amount_writable(document_id))"
        " WITH CHECK (app.document_amount_writable(document_id))"
    )

    # Счётчик номеров приложению не виден и не меняется напрямую: только функция next_document_number.
    op.execute(
        "CREATE POLICY document_number_counter_none ON document_number_counter FOR ALL TO kut_app"
        " USING (false) WITH CHECK (false)"
    )

    # F1 для документов: суммы в журнале аудита - только с правом на финансы документов.
    op.execute(
        "CREATE POLICY audit_log_document_amount ON audit_log AS RESTRICTIVE FOR SELECT TO kut_app"
        f" USING (table_name IS DISTINCT FROM 'document_amount' OR {CAN_READ_FINANCIAL})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_log_document_amount ON audit_log")
    for name, table in (
        ("document_number_counter_none", "document_number_counter"),
        ("document_amount_update_app", "document_amount"),
        ("document_amount_insert_app", "document_amount"),
        ("document_amount_select_portal", "document_amount"),
        ("document_amount_select_app", "document_amount"),
        ("document_link_insert_app", "document_link"),
        ("document_link_select_app", "document_link"),
        ("document_status_event_select_app", "document_status_event"),
        ("document_version_insert_app", "document_version"),
        ("document_version_select_portal", "document_version"),
        ("document_version_select_app", "document_version"),
        ("document_update_app", "document"),
        ("document_insert_app", "document"),
        ("document_select_portal", "document"),
        ("document_select_app", "document"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    for trigger, table in (
        ("document_status_event_forbid", "document_status_event"),
        ("document_link_guard", "document_link"),
        ("document_amount_guard", "document_amount"),
        ("document_version_created", "document_version"),
        ("document_version_guard", "document_version"),
        ("document_guard", "document"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("SET LOCAL ROLE kut_auth")
    for signature in (
        "document_event_forbid()",
        "document_link_guard()",
        "document_amount_guard()",
        "document_version_created()",
        "document_version_guard()",
        "document_guard()",
        "document_version_transition(uuid, text, text)",
        "document_next_version(uuid)",
        "next_document_number(uuid, text, integer)",
        "document_archive(uuid)",
        "document_version_storage_key(uuid)",
        "document_amount_writable(uuid)",
        "document_writable(uuid)",
        "document_scope_writable(uuid, uuid)",
        "document_allowed(uuid)",
        "user_role_code()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS app.{signature}")
    op.execute("RESET ROLE")
