"""Scraping the permit database into S3."""

from .doc_parse import parse_doc_url

__all__ = ["parse_doc_url"]
