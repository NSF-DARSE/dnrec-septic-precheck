"""Fetching and parsing permit detail pages.

The detail page is the only place document links appear, so every document has
to come through here. Two behaviours of the remote site shape this module:

Rate limiting is global rather than per thread, because politeness is a
property of the whole crawl.

The Documents grid is served non-deterministically. Refetching the same permit
can return a page with the grid absent, which reads as zero documents. A single
pass therefore under-collects, and any count of zero needs confirming before it
is believed. FetchResult keeps the HTTP status and body length so callers can
tell "no documents" apart from "no grid".
"""
from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse as up
from dataclasses import dataclass, field

import requests

from .. import config
from .doc_parse import parse_doc_url

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
HREF_RE = re.compile(r'href\s*=\s*"([^"]+)"', re.I)
TAGS_RE = re.compile(r"<[^>]+>")
MARKER_RE = re.compile(r"</?[IRG]>")
ID_RE = re.compile(r"[?&]id=(\d+)", re.I)

RETRY_STATUS = (429, 500, 502, 503, 504)


class RateLimiter:
    """Global minimum interval between outbound requests."""

    def __init__(self, min_interval: float = config.MIN_REQUEST_INTERVAL):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if delay:
            time.sleep(delay)


@dataclass
class FetchResult:
    """Outcome of one HTTP fetch, including the failure cases."""

    url: str
    ok: bool
    status_code: int | None = None
    text: str = ""
    content: bytes = b""
    error: str | None = None
    attempts: int = 0

    @property
    def length(self) -> int:
        return len(self.content) if self.content else len(self.text)


class Fetcher:
    """Rate limited HTTP client with bounded retries and per thread sessions."""

    def __init__(self, limiter: RateLimiter | None = None, tries: int = 4,
                 timeout: int = 180):
        self.limiter = limiter or RateLimiter()
        self.tries = tries
        self.timeout = timeout
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": config.USER_AGENT})
            self._local.session = s
        return s

    def get(self, url: str, want_bytes: bool = False) -> FetchResult:
        last = None
        status = None
        for attempt in range(1, self.tries + 1):
            self.limiter.wait()
            try:
                r = self.session.get(url, timeout=self.timeout)
                status = r.status_code
                if r.status_code == 200:
                    return FetchResult(
                        url=url,
                        ok=True,
                        status_code=200,
                        text="" if want_bytes else r.text,
                        content=r.content if want_bytes else b"",
                        attempts=attempt,
                    )
                last = f"HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUS:
                    break
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            if attempt < self.tries:
                time.sleep(2 ** (attempt - 1))
        return FetchResult(url=url, ok=False, status_code=status, error=last,
                           attempts=self.tries)


def detail_url(detail_id: str | int) -> str:
    return config.DETAIL_URL.format(id=detail_id)


def detail_id_from_url(url: str) -> str | None:
    m = ID_RE.search(url or "")
    return m.group(1) if m else None


def clean_text(cell: str) -> str:
    """Flatten one grid cell to plain text.

    The grid renders anchor text percent encoded, so it needs unquoting before
    it is readable. This output is kept for debugging only. Classification comes
    from the URL, never from here.
    """
    s = html.unescape(cell)
    s = TAGS_RE.sub(" ", s)
    s = up.unquote(s)
    s = MARKER_RE.sub(" ", s)
    return " ".join(s.split())


def parse_documents(page_html: str) -> list[dict]:
    """Extract document rows from the Documents grid.

    Fields come from parse_doc_url. The grid cell text is carried along under
    doc_name_raw so a human can inspect it, but it is not used to decide the
    document type.
    """
    docs: list[dict] = []
    seen: set[str] = set()

    for row in ROW_RE.findall(page_html):
        if config.DOC_HOST not in row:
            continue
        hrefs = [h for h in HREF_RE.findall(row) if config.DOC_HOST in h]
        if not hrefs:
            continue
        url = html.unescape(hrefs[0])
        if url in seen:
            continue
        seen.add(url)

        cells = [clean_text(c) for c in CELL_RE.findall(row)]
        parsed = parse_doc_url(url)
        docs.append(
            {
                "url": url,
                "doctype": parsed["doc_type"] or "Other",
                "program": parsed["program"],
                "permit_number": parsed["permit_number"],
                "description": parsed["description"],
                "parcel_id": parsed["parcel_id"],
                "foia": parsed["foia"],
                "title": parsed["title_raw"] or "",
                "doc_date_raw": cells[2] if len(cells) > 2 else "",
                "doc_name_raw": cells[0] if cells else "",
            }
        )
    return docs


def has_document_grid(page_html: str) -> bool:
    """Whether the page contains the document host at all.

    Distinguishes a permit with no documents from a response served without the
    grid. See the module docstring on non-determinism.
    """
    return config.DOC_HOST in (page_html or "")


@dataclass
class DetailPage:
    detail_id: str
    fetch: FetchResult
    documents: list[dict] = field(default_factory=list)
    grid_present: bool = False

    @property
    def outcome(self) -> str:
        if not self.fetch.ok:
            return "FETCH_FAILED"
        if self.documents:
            return "PARSED_DOCS"
        if self.grid_present:
            return "PARSED_ZERO_DOCS"
        return "GRID_ABSENT"


def load_detail(fetcher: Fetcher, detail_id: str | int) -> DetailPage:
    """Fetch one detail page and parse it, reporting failure distinctly."""
    did = str(detail_id)
    result = fetcher.get(detail_url(did))
    if not result.ok:
        return DetailPage(detail_id=did, fetch=result)
    return DetailPage(
        detail_id=did,
        fetch=result,
        documents=parse_documents(result.text),
        grid_present=has_document_grid(result.text),
    )
