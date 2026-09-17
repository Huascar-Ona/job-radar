"""Reconciling configured boards into the companies table.

`config/boards.yml` is the source of truth (ADR-0012); this table is derived
state that exists to give jobs and crawl outcomes a stable foreign key.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

_SYNC = text("""
INSERT INTO companies (source, board_token, display_name, is_enabled)
VALUES (:source, :board_token, :display_name, :enabled)
ON CONFLICT (source, board_token) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    is_enabled   = EXCLUDED.is_enabled,
    updated_at   = now()
RETURNING id
""")

# Disabled, never deleted: historical jobs and crawl outcomes keep their
# foreign keys (ADR-0012). The RESTRICT foreign keys would refuse a delete
# anyway; this makes the intent explicit rather than relying on that refusal.
_DISABLE_ABSENT = text("""
UPDATE companies
SET is_enabled = false, updated_at = now()
WHERE source = :source
  AND is_enabled
  AND NOT (board_token = ANY(:board_tokens))
""")

_ENABLED = text("""
SELECT id, source, board_token, display_name, created_at
FROM companies
WHERE is_enabled
ORDER BY source, board_token
""")


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    """A configured company as stored."""

    id: int
    source: str
    board_token: str
    display_name: str
    created_at: datetime


class CompanyRepository:
    """Reads and reconciles companies. Never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sync(
        self,
        *,
        source: str,
        board_token: str,
        display_name: str,
        enabled: bool,
    ) -> int:
        """Create or update one configured company, returning its id.

        Identity is `(source, board_token)`; `display_name` and `enabled` are
        mutable metadata, so a rename updates in place and never creates a row.
        """
        return int(
            self._session.execute(
                _SYNC,
                {
                    "source": str(source),
                    "board_token": board_token,
                    "display_name": display_name,
                    "enabled": enabled,
                },
            ).scalar_one()
        )

    def disable_absent(self, *, source: str, board_tokens: Iterable[str]) -> int:
        """Disable companies of ``source`` that configuration no longer lists.

        Returns the number disabled. Companies are never deleted.
        """
        result = self._session.execute(
            _DISABLE_ABSENT,
            {"source": str(source), "board_tokens": list(board_tokens)},
        )
        # CursorResult exposes rowcount; the Result protocol does not.
        return int(cast("CursorResult[Any]", result).rowcount)

    def enabled_companies(self) -> Sequence[CompanyRecord]:
        """Every enabled company, ordered deterministically."""
        return [
            CompanyRecord(
                id=int(row.id),
                source=row.source,
                board_token=row.board_token,
                display_name=row.display_name,
                created_at=row.created_at,
            )
            for row in self._session.execute(_ENABLED)
        ]
