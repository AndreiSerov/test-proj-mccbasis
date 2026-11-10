"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-11-10

"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Empty baseline: the schema arrives in later revisions."""


def downgrade() -> None:
    """Nothing to undo."""
