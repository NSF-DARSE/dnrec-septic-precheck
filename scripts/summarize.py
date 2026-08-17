"""Reconcile the manifest against S3 and write the audit report."""
import sys

import _bootstrap  # noqa: F401

from septic.cli import main

if __name__ == "__main__":
    sys.exit(main(["audit"] + sys.argv[1:]))
