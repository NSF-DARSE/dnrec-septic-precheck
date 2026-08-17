"""Verify the AWS services the pipeline depends on."""
import sys

import _bootstrap  # noqa: F401

from septic.cli import main

if __name__ == "__main__":
    sys.exit(main(["preflight"] + sys.argv[1:]))
