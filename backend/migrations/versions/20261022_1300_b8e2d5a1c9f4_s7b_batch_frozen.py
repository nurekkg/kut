"""S7b (ревью кода, третий раунд): партия неизменяема после первого движения (ADR-016 часть 1).

Гард BEFORE UPDATE на material_batch: номер, сертификат, ГТД и страна не меняются, если по партии есть движения.
Функция - SECURITY DEFINER от kut_auth: движения видны ей без RLS, иначе смена номера прошла бы по скрытым движениям."""

from collections.abc import Sequence

from alembic import op

revision: str = "b8e2d5a1c9f4"
down_revision: str | Sequence[str] | None = "a4d7e1c9b3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD = """
CREATE OR REPLACE FUNCTION app.material_batch_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF (OLD.batch_no, OLD.certificate_document_id, OLD.customs_document_id, OLD.country_of_origin)
        IS DISTINCT FROM
       (NEW.batch_no, NEW.certificate_document_id, NEW.customs_document_id, NEW.country_of_origin)
       AND EXISTS (SELECT 1 FROM public.stock_movement m WHERE m.batch_id = OLD.id) THEN
        RAISE EXCEPTION 'batch.frozen';
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(GUARD)
    op.execute("GRANT EXECUTE ON FUNCTION app.material_batch_guard() TO kut_owner")
    op.execute("RESET ROLE")
    op.execute(
        "CREATE TRIGGER material_batch_frozen BEFORE UPDATE ON material_batch"
        " FOR EACH ROW EXECUTE FUNCTION app.material_batch_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS material_batch_frozen ON material_batch")
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.material_batch_guard()")
    op.execute("RESET ROLE")
