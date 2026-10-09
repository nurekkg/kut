"""work_actual (факт работ) and work_photo (ТЗ §5.3, §5.14)

Revision ID: e1b7c4d93a86
Revises: d6a3f8b52e19
Create Date: 2026-10-02 14:30:00.000000

Факт работ: статусы draft -> submitted -> approved (или rejected с причиной), в расчёт готовности
идёт только approved. Переходы статусов и права на них живут в БД (триггер app.work_actual_guard
по праву из app.has_permission), а не только в сервисе: мастер вносит и отправляет, прораб и
техдиректор подтверждают и отклоняют, их собственный факт сразу approved, после approved правит
только техдиректор (work_actual:write:after_approval); каждая правка попадает в audit_log.

Фото: метаданные в БД, файл в S3 (ключ начинается с project/<id объекта>/, это проверяет CHECK).
Фото закрыты для внешних ролей (в MVP их видят только внутренние).

Точки расширения S2 снова использованы: project_node_has_dependents, schedule_item_has_dependents и
work_has_dependents теперь учитывают факт (CREATE OR REPLACE).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.helpers import business_table, create_audit_triggers, force_rls, grant

revision: str = "e1b7c4d93a86"
down_revision: str | Sequence[str] | None = "d6a3f8b52e19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READ_WRITE = "SELECT, INSERT, UPDATE"
WRITERS = "kut_app, kut_worker"
LOGINS = "kut_app, kut_worker, kut_portal"
INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
CAN_READ_FACTS = "(SELECT app.has_permission('work_actual', 'read'))"

STATUSES = ("draft", "submitted", "approved", "rejected")
CONTENT_TYPES = ("image/jpeg", "image/png", "image/heic", "image/webp")
MAX_PHOTO_BYTES = 15 * 1024 * 1024

# Внешним ролям - только подтверждённый факт и без людей (автор, подтвердивший, геометки).
PORTAL_ACTUAL_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "project_id",
    "node_id",
    "work_id",
    "schedule_item_id",
    "volume",
    "work_date",
    "status",
    "approved_at",
)

PHOTO_FROZEN = (
    "project_id",
    "legal_entity_id",
    "work_actual_id",
    "s3_key",
    "content_type",
    "size_bytes",
    "latitude",
    "longitude",
    "taken_at",
    "uploaded_by",
)

# Дата ведётся по поясу Кыргызстана (UTC+6, без перехода на летнее время): факт, внесённый вечером
# по местному времени, не должен считаться «будущим» из-за UTC на сервере.
LOCAL_TODAY = """
CREATE FUNCTION app.local_today() RETURNS date LANGUAGE sql STABLE SET search_path = pg_catalog AS
$$ SELECT (now() AT TIME ZONE 'Asia/Bishkek')::date $$
"""

# ТОЧКИ РАСШИРЕНИЯ (S2): живые связи, мешающие удалению. Версии S2/ГПР - в downgrade.
NODE_HAS_DEPENDENTS = """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.node_id = p_node AND item.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.node_id = p_node AND fact.deleted_at IS NULL)
$$
"""
NODE_HAS_DEPENDENTS_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.project_node child
        WHERE child.parent_id = p_node AND child.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.node_id = p_node AND item.deleted_at IS NULL)
$$
"""

ITEM_HAS_DEPENDENTS = """
CREATE OR REPLACE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.schedule_item_id = p_item AND fact.deleted_at IS NULL)
$$
"""
ITEM_HAS_DEPENDENTS_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.schedule_item_has_dependents(p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_dependency dep
        WHERE (dep.predecessor_id = p_item OR dep.successor_id = p_item)
          AND dep.deleted_at IS NULL)
$$
"""

WORK_HAS_DEPENDENTS = """
CREATE OR REPLACE FUNCTION app.work_has_dependents(p_work uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.work_id = p_work AND item.deleted_at IS NULL)
        OR EXISTS (
        SELECT 1 FROM public.work_actual fact
        WHERE fact.work_id = p_work AND fact.deleted_at IS NULL)
$$
"""
WORK_HAS_DEPENDENTS_PREVIOUS = """
CREATE OR REPLACE FUNCTION app.work_has_dependents(p_work uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schedule_item item
        WHERE item.work_id = p_work AND item.deleted_at IS NULL)
$$
"""

ACTUAL_HAS_DEPENDENTS = """
CREATE FUNCTION app.work_actual_has_dependents(p_fact uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.work_photo photo
        WHERE photo.work_actual_id = p_fact AND photo.deleted_at IS NULL)
$$
"""

# Автомат фактов. v_user NULL - системный контекст (сид, воркер): права не проверяются, но CHECK
# таблицы (согласованность статуса и полей) действуют для всех. Пользователь не может сам записать
# автора, подтвердившего, время и причину: они вычисляются здесь.
WORK_ACTUAL_GUARD = """
CREATE FUNCTION app.work_actual_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    v_user uuid := app.user_id();
    v_can_write boolean := false;
    v_can_approve boolean := false;
    v_can_edit_approved boolean := false;
    v_status_changed boolean;
    v_content_changed boolean;
BEGIN
    IF v_user IS NOT NULL THEN
        v_can_write := app.has_permission('work_actual', 'write');
        v_can_approve := app.has_permission('work_actual', 'approve');
        v_can_edit_approved := app.has_permission('work_actual', 'write', 'after_approval');
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF v_user IS NOT NULL THEN
            IF NOT v_can_write THEN
                RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
            END IF;
            NEW.author_id := v_user;
            IF v_can_approve THEN
                NEW.status := 'approved';
                NEW.approved_by := v_user;
                NEW.approved_at := now();
                NEW.reject_reason := NULL;
            ELSIF NEW.status NOT IN ('draft', 'submitted') THEN
                RAISE EXCEPTION 'actual.transition_forbidden' USING ERRCODE = 'check_violation';
            ELSE
                NEW.approved_by := NULL;
                NEW.approved_at := NULL;
                NEW.reject_reason := NULL;
            END IF;
        END IF;
        IF NEW.work_date > app.local_today() THEN
            RAISE EXCEPTION 'actual.date_in_future' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.project_node n
            WHERE n.id = NEW.node_id AND n.project_id = NEW.project_id AND n.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'actual.node_invalid' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.work w WHERE w.id = NEW.work_id AND w.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'actual.work_invalid' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.schedule_item_id IS NULL THEN
            SELECT item.id INTO NEW.schedule_item_id FROM public.schedule_item item
            WHERE item.project_id = NEW.project_id AND item.node_id = NEW.node_id
              AND item.work_id = NEW.work_id AND item.deleted_at IS NULL;
        ELSIF NOT EXISTS (
            SELECT 1 FROM public.schedule_item item
            WHERE item.id = NEW.schedule_item_id AND item.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'actual.item_invalid' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.deleted_at IS NOT NULL AND v_user IS NOT NULL THEN
        RAISE EXCEPTION 'actual.locked' USING ERRCODE = 'check_violation';
    END IF;
    v_status_changed := NEW.status IS DISTINCT FROM OLD.status;
    v_content_changed := (NEW.node_id, NEW.work_id, NEW.schedule_item_id, NEW.volume,
                          NEW.work_date, NEW.latitude, NEW.longitude, NEW.accuracy_m)
        IS DISTINCT FROM (OLD.node_id, OLD.work_id, OLD.schedule_item_id, OLD.volume,
                          OLD.work_date, OLD.latitude, OLD.longitude, OLD.accuracy_m);
    IF v_user IS NOT NULL AND NOT v_status_changed THEN
        NEW.approved_by := OLD.approved_by;
        NEW.approved_at := OLD.approved_at;
        NEW.reject_reason := OLD.reject_reason;
    END IF;

    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
        IF v_status_changed OR v_content_changed THEN
            RAISE EXCEPTION 'actual.locked' USING ERRCODE = 'check_violation';
        END IF;
        IF v_user IS NOT NULL AND NOT (
            (OLD.status IN ('draft', 'rejected') AND v_can_write AND v_user = OLD.author_id)
            OR v_can_edit_approved
        ) THEN
            RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        IF app.work_actual_has_dependents(OLD.id) THEN
            RAISE EXCEPTION 'actual.has_dependents' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF v_user IS NOT NULL THEN
        IF NOT v_status_changed THEN
            IF v_content_changed THEN
                IF OLD.status IN ('draft', 'rejected') THEN
                    IF NOT (v_can_write AND v_user = OLD.author_id) THEN
                        RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
                    END IF;
                ELSIF OLD.status = 'approved' THEN
                    IF NOT v_can_edit_approved THEN
                        RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'actual.locked' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
        ELSIF OLD.status IN ('draft', 'rejected') AND NEW.status = 'submitted' THEN
            IF NOT (v_can_write AND v_user = OLD.author_id) THEN
                RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
            END IF;
            NEW.approved_by := NULL;
            NEW.approved_at := NULL;
            NEW.reject_reason := NULL;
        ELSIF OLD.status = 'submitted' AND NEW.status IN ('approved', 'rejected') THEN
            IF NOT v_can_approve THEN
                RAISE EXCEPTION 'actual.forbidden' USING ERRCODE = 'check_violation';
            END IF;
            IF v_content_changed THEN
                RAISE EXCEPTION 'actual.locked' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = 'approved' THEN
                NEW.approved_by := v_user;
                NEW.approved_at := now();
                NEW.reject_reason := NULL;
            ELSE
                IF NEW.reject_reason IS NULL OR btrim(NEW.reject_reason) = '' THEN
                    RAISE EXCEPTION 'actual.reason_required' USING ERRCODE = 'check_violation';
                END IF;
                NEW.approved_by := NULL;
                NEW.approved_at := NULL;
            END IF;
        ELSE
            RAISE EXCEPTION 'actual.transition_forbidden' USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.work_date IS DISTINCT FROM OLD.work_date AND NEW.work_date > app.local_today() THEN
        RAISE EXCEPTION 'actual.date_in_future' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.node_id IS DISTINCT FROM OLD.node_id AND NOT EXISTS (
        SELECT 1 FROM public.project_node n
        WHERE n.id = NEW.node_id AND n.project_id = NEW.project_id AND n.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'actual.node_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.work_id IS DISTINCT FROM OLD.work_id AND NOT EXISTS (
        SELECT 1 FROM public.work w WHERE w.id = NEW.work_id AND w.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'actual.work_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.node_id, NEW.work_id) IS DISTINCT FROM (OLD.node_id, OLD.work_id)
       AND NEW.schedule_item_id IS NOT DISTINCT FROM OLD.schedule_item_id THEN
        SELECT item.id INTO NEW.schedule_item_id FROM public.schedule_item item
        WHERE item.project_id = NEW.project_id AND item.node_id = NEW.node_id
          AND item.work_id = NEW.work_id AND item.deleted_at IS NULL;
    ELSIF NEW.schedule_item_id IS NOT NULL
          AND NEW.schedule_item_id IS DISTINCT FROM OLD.schedule_item_id
          AND NOT EXISTS (
              SELECT 1 FROM public.schedule_item item
              WHERE item.id = NEW.schedule_item_id AND item.deleted_at IS NULL
          ) THEN
        RAISE EXCEPTION 'actual.item_invalid' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""

# Право добавить или убрать фото у факта. Одно место для триггера и для проверки перед выдачей
# ссылки на загрузку. Фото добавляет автор, пока факт не отправлен на проверку (draft, rejected) и
# после согласования только в собственный факт, который он согласовал сам (факт прораба и
# техдиректора согласован сразу); чужое согласованное доказательство меняет только техдиректор.
# Убирает фото автор в draft и rejected, техдиректор - всегда. Отправленный (submitted) факт заперт.
WORK_PHOTO_RIGHTS = """
CREATE FUNCTION app.work_photo_rights(p_fact uuid, p_op text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT CASE WHEN app.user_id() IS NULL THEN true ELSE COALESCE((
        SELECT CASE p_op
            WHEN 'add' THEN
                (f.author_id = app.user_id() AND app.has_permission('work_actual', 'write')
                 AND (f.status IN ('draft', 'rejected')
                      OR (f.status = 'approved' AND f.approved_by = f.author_id)))
                OR (f.status = 'approved'
                    AND app.has_permission('work_actual', 'write', 'after_approval'))
            WHEN 'delete' THEN
                (f.status IN ('draft', 'rejected') AND f.author_id = app.user_id()
                 AND app.has_permission('work_actual', 'write'))
                OR app.has_permission('work_actual', 'write', 'after_approval')
            ELSE false
        END
        FROM public.work_actual f WHERE f.id = p_fact AND f.deleted_at IS NULL
    ), false) END
$$
"""

WORK_PHOTO_GUARD = """
CREATE FUNCTION app.work_photo_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF app.user_id() IS NOT NULL THEN
            NEW.uploaded_by := app.user_id();
        END IF;
        IF NOT app.work_photo_rights(NEW.work_actual_id, 'add') THEN
            RAISE EXCEPTION 'photo.forbidden' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.deleted_at IS NOT NULL AND app.user_id() IS NOT NULL THEN
        RAISE EXCEPTION 'photo.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL
       AND NOT app.work_photo_rights(OLD.work_actual_id, 'delete') THEN
        RAISE EXCEPTION 'photo.forbidden' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""


def upgrade() -> None:
    op.execute(LOCAL_TODAY)
    op.execute("REVOKE ALL ON FUNCTION app.local_today() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.local_today() TO {LOGINS}")

    statuses = ", ".join(f"'{status}'" for status in STATUSES)
    business_table(
        "work_actual",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_item_id", sa.Uuid(), nullable=True),
        sa.Column("volume", sa.Numeric(18, 3), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("accuracy_m", sa.Numeric(8, 2), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_work_actual_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "node_id"],
            ["project_node.project_id", "project_node.id"],
            name="fk_work_actual_project_id_project_node",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["work.id"], name=op.f("fk_work_actual_work_id_work")),
        # Факт, привязанный к строке ГПР, лежит в том же объекте на том же узле и по тому же виду
        # работ. У факта без плана schedule_item_id пуст, и проверка не срабатывает (MATCH SIMPLE).
        sa.ForeignKeyConstraint(
            ["project_id", "schedule_item_id", "node_id", "work_id"],
            [
                "schedule_item.project_id",
                "schedule_item.id",
                "schedule_item.node_id",
                "schedule_item.work_id",
            ],
            name="fk_work_actual_project_id_schedule_item",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["user.id"], name=op.f("fk_work_actual_author_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["user.id"], name=op.f("fk_work_actual_approved_by_user")
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_work_actual_project_id_id"),
        sa.CheckConstraint(f"status IN ({statuses})", name=op.f("ck_work_actual_status_valid")),
        sa.CheckConstraint("volume > 0", name=op.f("ck_work_actual_volume_positive")),
        sa.CheckConstraint(
            "(status = 'approved') = (approved_by IS NOT NULL)"
            " AND (approved_by IS NULL) = (approved_at IS NULL)",
            name=op.f("ck_work_actual_approval_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)"
            " AND (reject_reason IS NULL OR btrim(reject_reason) <> '')",
            name=op.f("ck_work_actual_rejection_consistent"),
        ),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name=op.f("ck_work_actual_coordinates_valid"),
        ),
        sa.CheckConstraint(
            "accuracy_m IS NULL OR (latitude IS NOT NULL AND accuracy_m >= 0"
            " AND accuracy_m <= 100000)",
            name=op.f("ck_work_actual_accuracy_valid"),
        ),
    )
    op.create_index("ix_work_actual_node_id_work_date", "work_actual", ["node_id", "work_date"])
    op.create_index(
        "ix_work_actual_schedule_item_id",
        "work_actual",
        ["schedule_item_id"],
        postgresql_where=sa.text("schedule_item_id IS NOT NULL"),
    )
    op.create_index("ix_work_actual_project_id_status", "work_actual", ["project_id", "status"])
    op.create_index("ix_work_actual_work_id", "work_actual", ["work_id"])
    op.create_index("ix_work_actual_author_id", "work_actual", ["author_id"])
    op.create_index("ix_work_actual_legal_entity_id", "work_actual", ["legal_entity_id"])

    content_types = ", ".join(f"'{kind}'" for kind in CONTENT_TYPES)
    business_table(
        "work_photo",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("work_actual_id", sa.Uuid(), nullable=False),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "legal_entity_id"],
            ["project.id", "project.legal_entity_id"],
            name="fk_work_photo_project_id_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "work_actual_id"],
            ["work_actual.project_id", "work_actual.id"],
            name="fk_work_photo_project_id_work_actual",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["user.id"], name=op.f("fk_work_photo_uploaded_by_user")
        ),
        sa.UniqueConstraint("s3_key", name="uq_work_photo_s3_key"),
        sa.CheckConstraint(
            f"content_type IN ({content_types})", name=op.f("ck_work_photo_content_type_valid")
        ),
        sa.CheckConstraint(
            f"size_bytes > 0 AND size_bytes <= {MAX_PHOTO_BYTES}",
            name=op.f("ck_work_photo_size_valid"),
        ),
        sa.CheckConstraint(
            "starts_with(s3_key, 'project/' || project_id::text || '/')",
            name=op.f("ck_work_photo_key_in_project"),
        ),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)"
            " AND (latitude IS NULL OR (latitude BETWEEN -90 AND 90"
            " AND longitude BETWEEN -180 AND 180))",
            name=op.f("ck_work_photo_coordinates_valid"),
        ),
    )
    op.create_index(
        "ix_work_photo_project_id_work_actual_id", "work_photo", ["project_id", "work_actual_id"]
    )
    op.create_index("ix_work_photo_legal_entity_id", "work_photo", ["legal_entity_id"])
    op.create_index("ix_work_photo_uploaded_by", "work_photo", ["uploaded_by"])

    op.execute("GRANT SELECT ON work_actual, work_photo TO kut_auth")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(ACTUAL_HAS_DEPENDENTS)
    op.execute("REVOKE ALL ON FUNCTION app.work_actual_has_dependents(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.work_actual_has_dependents(uuid) TO {WRITERS}")
    for sql in (NODE_HAS_DEPENDENTS, ITEM_HAS_DEPENDENTS, WORK_HAS_DEPENDENTS):
        op.execute(sql)
    op.execute("RESET ROLE")

    op.execute(WORK_ACTUAL_GUARD)
    op.execute(WORK_PHOTO_RIGHTS)
    op.execute(WORK_PHOTO_GUARD)
    op.execute("REVOKE ALL ON FUNCTION app.work_photo_rights(uuid, text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.work_photo_rights(uuid, text) TO {WRITERS}")
    op.execute(
        "CREATE TRIGGER work_actual_guard BEFORE INSERT OR UPDATE ON work_actual"
        " FOR EACH ROW EXECUTE FUNCTION app.work_actual_guard()"
    )
    op.execute(
        "CREATE TRIGGER work_photo_guard BEFORE INSERT OR UPDATE ON work_photo"
        " FOR EACH ROW EXECUTE FUNCTION app.work_photo_guard()"
    )

    for table in ("work_actual", "work_photo"):
        force_rls(table)
        visible = f"{INTERNAL} AND project_id = ANY {PROJECTS}"
        if table == "work_photo":
            # На снимках люди и техника, как и в API нужно модульное право чтения факта. Для самого
            # факта это условие нельзя: его строки считают готовность для ролей с одним gpr:read.
            visible += f" AND {CAN_READ_FACTS}"
        op.execute(
            f"CREATE POLICY {table}_select_app ON {table} FOR SELECT TO kut_app USING ({visible})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert_app ON {table} FOR INSERT TO kut_app"
            f" WITH CHECK ({INTERNAL} AND project_id = ANY {PROJECTS})"
        )
        op.execute(
            f"CREATE POLICY {table}_update_app ON {table} FOR UPDATE TO kut_app"
            f" USING ({INTERNAL} AND project_id = ANY {PROJECTS})"
            f" WITH CHECK ({INTERNAL} AND project_id = ANY {PROJECTS})"
        )
    op.execute(
        "CREATE POLICY work_actual_select_portal ON work_actual FOR SELECT TO kut_portal"
        f" USING (project_id = ANY {PROJECTS} AND status = 'approved' AND deleted_at IS NULL)"
    )
    grant("work_actual", app=READ_WRITE, worker=READ_WRITE, portal_columns=PORTAL_ACTUAL_COLUMNS)
    grant("work_photo", app=READ_WRITE, worker=READ_WRITE)

    create_audit_triggers(
        "work_actual",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=("project_id", "legal_entity_id", "author_id"),
    )
    create_audit_triggers(
        "work_photo",
        project_column="project_id",
        legal_entity_column="legal_entity_id",
        frozen_columns=PHOTO_FROZEN,
    )


def downgrade() -> None:
    op.drop_table("work_photo")
    op.drop_table("work_actual")
    op.execute("DROP FUNCTION app.work_photo_guard()")
    op.execute("DROP FUNCTION app.work_photo_rights(uuid, text)")
    op.execute("DROP FUNCTION app.work_actual_guard()")
    op.execute("SET LOCAL ROLE kut_auth")
    for sql in (
        NODE_HAS_DEPENDENTS_PREVIOUS,
        ITEM_HAS_DEPENDENTS_PREVIOUS,
        WORK_HAS_DEPENDENTS_PREVIOUS,
    ):
        op.execute(sql)
    op.execute("DROP FUNCTION app.work_actual_has_dependents(uuid)")
    op.execute("RESET ROLE")
    op.execute("DROP FUNCTION app.local_today()")
