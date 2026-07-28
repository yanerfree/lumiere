"""merge i18n/ai-capability head with protocol-mock-lock head

Revision ID: h2b3c4d5e6f7
Revises: c4e6d8f0a2b1, a3b4c5d6e7f8
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h2b3c4d5e6f7'
down_revision: Union[str, None] = ('c4e6d8f0a2b1', 'a3b4c5d6e7f8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
