"""The collector and normalizer interfaces.

A collector owns transport and provider retrieval, nothing else. It returns
provider payloads unchanged; converting them is the normalizer's job, and
persisting them is nobody's business at this layer (ADR-0016).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from job_radar.domain.job import NormalizedJob


@runtime_checkable
class Collector(Protocol):
    """Retrieves the current postings of one configured board."""

    def fetch_jobs(self, *, board_token: str) -> Sequence[object]:
        """Return the provider's postings for ``board_token``.

        The return type is deliberately loose. The collector guarantees only
        what the envelope guarantees: a list. Whether any given element is a
        convertible posting is a per-posting question, and answering it here
        would let one malformed entry fail an otherwise good board (ADR-0016).

        Raises:
            CollectorError: the provider did not deliver a usable board.
        """
        ...


@runtime_checkable
class Normalizer(Protocol):
    """Translates one provider posting into a NormalizedJob.

    One implementation per source. There is deliberately no shared normalizer
    that branches on source (ARCHITECTURE, "Architectural Boundary").
    """

    def normalize(
        self, posting: object, *, company: str, observed_at: datetime
    ) -> NormalizedJob:
        """Translate one element of what the collector returned.

        Pure: no I/O and no clock. ``company`` is the configured display name
        (ADR-0007) and ``observed_at`` the crawl's instant from the injected
        clock (ADR-0003).

        Raises:
            NormalizationError: this posting cannot be converted. The caller
                counts it as ``invalid`` and carries on with the board.
            ValueError: the arguments are unusable. That is a caller bug, not
                an invalid posting.
        """
        ...
