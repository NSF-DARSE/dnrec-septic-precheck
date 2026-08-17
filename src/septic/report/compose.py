"""Assembling the report content from a rule Report.

Composition is deliberately separate from rendering so the verdict and the
itemised findings can be tested without a template. Text generation on Bedrock is
denied for this account, so any wording pass has to run through the fallback
provider or be omitted.

Not implemented yet, pending sign off.
"""
from __future__ import annotations

PENDING = "report design not yet approved"


def compose(report) -> dict:
    raise NotImplementedError(PENDING)
