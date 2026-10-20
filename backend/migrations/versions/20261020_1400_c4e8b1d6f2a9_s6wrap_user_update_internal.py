"""S6-wrap: user_update_app снова требует внутреннего пользователя (защита в глубину)

Revision ID: c4e8b1d6f2a9
Revises: b8d2e4f6a9c1
Create Date: 2026-10-20 14:00:00

Ревизия b8d2 заменила политику user_update_app условием "себе или владельцу" и потеряла app.is_internal() из S1a.
Теперь строку "user" меняет только внутренний пользователь: себя, либо любую строку как владелец. Внешний
(портал) не меняет ничего, даже свою строку.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8b1d6f2a9"
down_revision: str | Sequence[str] | None = "b8d2e4f6a9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SELF_OR_OWNER = "id = (SELECT app.user_id()) OR (SELECT app.user_role_code()) = 'owner'"


def upgrade() -> None:
    op.execute('DROP POLICY user_update_app ON public."user"')
    op.execute(
        'CREATE POLICY user_update_app ON public."user" FOR UPDATE TO kut_app '
        f"USING ((SELECT app.is_internal()) AND ({SELF_OR_OWNER})) "
        f"WITH CHECK ((SELECT app.is_internal()) AND ({SELF_OR_OWNER}))"
    )


def downgrade() -> None:
    op.execute('DROP POLICY user_update_app ON public."user"')
    op.execute(
        'CREATE POLICY user_update_app ON public."user" FOR UPDATE TO kut_app '
        f"USING ({SELF_OR_OWNER}) WITH CHECK ({SELF_OR_OWNER})"
    )
