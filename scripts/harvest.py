"""Harvest permit documents into S3."""
import sys

import _bootstrap  # noqa: F401

from septic.harvest.cli import main

if __name__ == "__main__":
    sys.exit(main())
