"""Add persisted, inspection-scoped OCR/document evidence."""
from alembic import op
import sqlalchemy as sa

revision = "e6201020a001"
down_revision = "c4e1a9d2b7f3"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "text_evidence",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("inspection_id", sa.String(32), sa.ForeignKey("inspections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("document_revision", sa.String(120)),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("mime", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("engine", sa.String(120), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("corrected_text", sa.Text()),
        sa.Column("confirmed_by", sa.String(32), sa.ForeignKey("users.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(40)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inspection_id", "sha256", name="uq_text_evidence_source"),
        sa.CheckConstraint("status IN ('completed','no_text','failed')", name="ck_text_evidence_status"),
    )
    op.create_index("ix_text_evidence_inspection_id", "text_evidence", ["inspection_id"])
    op.create_index("ix_text_evidence_workspace_id", "text_evidence", ["workspace_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM text_evidence")).scalar():
        raise RuntimeError("Refusing to discard stored text evidence. Restore a verified backup instead.")
    op.drop_table("text_evidence")
