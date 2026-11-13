"""The collector and normalizer interfaces.

A collector owns transport and provider retrieval, nothing else. It returns
provider payloads unchanged; converting them is the normalizer's job, and
persisting them is nobody's business at this layer (ADR-0016).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from job_radar.domain.job import NormalizedJob


@dataclass(frozen=True, slots=True)
class FetchedBoard:
    """What one fetch of a board returned, and whether it is known complete.

    ``completeness_verified`` is true only when the provider itself confirmed
    that ``postings`` is the whole board (for Greenhouse, ``meta.total``
    present and equal to the number of postings). Deactivation reasons about
    absence only from a verified fetch (ADR-0017).
    """

    postings: Sequence[object]
    completeness_verified: bool


@runtime_checkable
class Collector(Protocol):
    """Retrieves the current postings of one configured board."""

    def fetch_jobs(self, *, board_token: str) -> FetchedBoard:
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
                counts it as ``invalid`` and carries on with the board. Its
                ``source_job_id``, when not ``None``, must be exactly the id
                a valid posting would store; when that cannot be guaranteed
                it must be ``None`` (ADR-0017).
            ValueError: the arguments are unusable. That is a caller bug, not
                an invalid posting.
        """
        ...
