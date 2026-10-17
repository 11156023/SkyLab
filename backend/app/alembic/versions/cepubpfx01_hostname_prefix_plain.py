"""Turn course publication hostname templates into plain URL prefixes.

The per-student hostname used to come from a template containing ``{student}``
(and optionally ``{class}``); it is now ``<prefix>-<16 random hex chars>``.
Strip the placeholders out of existing rows so they still validate when a
teacher edits the environment. Nothing to restore on downgrade: the old
templates cannot be rebuilt from the prefixes.

Revision ID: cepubpfx01
Revises: mrg13_merge_platip_tjactive
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "cepubpfx01"
down_revision = "mrg13_merge_platip_tjactive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE course_environment_publications
        SET hostname_prefix = COALESCE(
            NULLIF(
                trim(
                    both '-' FROM regexp_replace(
                        regexp_replace(
                            lower(
                                regexp_replace(
                                    hostname_prefix, '\{(student|class)\}', '', 'g'
                                )
                            ),
                            '[^a-z0-9-]+', '-', 'g'
                        ),
                        '-{2,}', '-', 'g'
                    )
                ),
                ''
            ),
            'app'
        )
        WHERE mode = 'domain' AND hostname_prefix IS NOT NULL
        """
    )
    # Anything still longer than the new 40-character cap is trimmed; the hostname
    # gets a random suffix at class time anyway, so no two students collide.
    op.execute(
        """
        UPDATE course_environment_publications
        SET hostname_prefix = trim(both '-' FROM left(hostname_prefix, 40))
        WHERE mode = 'domain' AND length(hostname_prefix) > 40
        """
    )


def downgrade() -> None:
    pass
