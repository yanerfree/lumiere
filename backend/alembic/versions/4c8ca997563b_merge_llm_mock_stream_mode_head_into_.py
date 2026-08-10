"""merge llm-mock stream_mode head into main chain

Revision ID: 4c8ca997563b
Revises: m7f8a9b0c1d2, v6d7e8f9a0b1
Create Date: 2026-08-10 12:39:22.824365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c8ca997563b'
down_revision: Union[str, None] = ('m7f8a9b0c1d2', 'v6d7e8f9a0b1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
