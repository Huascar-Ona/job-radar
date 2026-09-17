"""Packaging entry point.

Deliberately minimal: the ingestion command is added in a later step.
"""

import sys

from job_radar import __version__


def main() -> int:
    """Print the installed version. Returns the process exit code."""
    sys.stdout.write(f"job-radar {__version__}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
