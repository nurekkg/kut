"""S7b (ревью кода, второй раунд): партии - вставка для каталога и кладовщика, обновление только каталогу;
функция «есть движения материала» для проверки смены флага сертификата.

Политика FOR ALL уступает место INSERT и UPDATE: кладовщик не правит существующие партии и их сертификат."""

from collections.abc import Sequence

from alembic import op

revision: str = "a4d7e1c9b3f5"
down_revision: str | Sequence[str] | None = "f3b8d1e6a4c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATALOG_OR_BATCH = (
    "(SELECT app.is_internal()) AND ((SELECT app.has_permission('materials', 'write', 'catalog'))"
    " OR (SELECT app.has_permission('materials', 'write', 'batch')))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
)
CATALOG_ONLY = (
    "(SELECT app.is_internal()) AND (SELECT app.has_permission('materials', 'write', 'catalog'))"
    " AND legal_entity_id = ANY ((SELECT app.legal_entity_ids())::uuid[]))"
)

MATERIAL_HAS_MOVEMENTS = """
CREATE OR REPLACE FUNCTION app.material_has_movements(p_material uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM public.stock_movement m WHERE m.material_id = p_material)
$$
"""


def upgrade() -> None:
    op.execute("DROP POLICY material_batch_write_app ON material_batch")
    op.execute(
        "CREATE POLICY material_batch_insert_app ON material_batch FOR INSERT TO kut_app"
        f" WITH CHECK ({CATALOG_OR_BATCH}"
    )
    op.execute(
        "CREATE POLICY material_batch_update_app ON material_batch FOR UPDATE TO kut_app"
        f" USING ({CATALOG_ONLY} WITH CHECK ({CATALOG_ONLY}"
    )
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(MATERIAL_HAS_MOVEMENTS)
    op.execute("GRANT EXECUTE ON FUNCTION app.material_has_movements(uuid) TO kut_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION app.material_has_movements(uuid) FROM kut_app")
    op.execute("DROP FUNCTION IF EXISTS app.material_has_movements(uuid)")
    op.execute("RESET ROLE")
    op.execute("DROP POLICY material_batch_update_app ON material_batch")
    op.execute("DROP POLICY material_batch_insert_app ON material_batch")
    op.execute(
        "CREATE POLICY material_batch_write_app ON material_batch FOR ALL TO kut_app USING ("
        f" {CATALOG_ONLY} WITH CHECK ({CATALOG_ONLY}"
    )
