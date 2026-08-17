"""Turning rule results into a reviewer facing report.

The report presents what rules.evaluate already decided. It does not recompute or
soften the verdict, and a language model used for wording is given the verdict as
an input, never asked for it.
"""
