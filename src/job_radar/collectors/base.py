"""The collector interface.

A collector owns transport and provider retrieval, nothing else. It returns
provider payloads unchanged; converting them is the normalizer's job, and
persisting them is nobody's business at this layer (ADR-0016).
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


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
