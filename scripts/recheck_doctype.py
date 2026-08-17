"""Recheck permits recorded with zero documents.

Requires two agreeing responses before believing a count, because the Documents
grid is served non-deterministically.
"""
import sys

import _bootstrap  # noqa: F401

from septic.cli import main

if __name__ == "__main__":
    sys.exit(main(["verify"] + sys.argv[1:]))
