"""add ingestion job lease fields

Revision ID: 263e791b7093
Revises: 24f11cf16318
Create Date: 2026-10-14 11:33:34.383570

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '263e791b7093'
down_revision: Union[str, Sequence[str], None] = '24f11cf16318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column("lease_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "knowledge_ingestion_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("knowledge_ingestion_jobs", "lease_expires_at")
    op.drop_column("knowledge_ingestion_jobs", "lease_token")
