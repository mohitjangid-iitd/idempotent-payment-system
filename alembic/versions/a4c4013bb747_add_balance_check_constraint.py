"""add balance check constraint

Revision ID: a4c4013bb747
Revises: 7211d8c9a67b
Create Date: 2026-11-17 04:43:16.540519

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4c4013bb747'
down_revision: Union[str, Sequence[str], None] = '7211d8c9a67b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_user_balance_non_negative", "user_table", "balance >= 0"
    )

def downgrade() -> None:
    op.drop_constraint("ck_user_balance_non_negative", "user_table", type_="check")