"""baseline: empty service migration

Revision ID: bdb7518b9492
Revises:
Create Date: 2026-09-29 21:54:58.539755

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bdb7518b9492"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
