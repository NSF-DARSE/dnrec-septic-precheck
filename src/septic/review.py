"""The path from an application packet to a reviewer report.

    PDF or permit number
      -> Textract StartDocumentAnalysis, async, FORMS and TABLES
      -> field extraction into the parameters the rules name
      -> rule evaluation against rules_7101.yaml
      -> report composition
      -> text and HTML

Correctness does not depend on the network. Textract output is cached to disk
keyed by the SHA256 of the document, so a second run on the same file needs no
AWS at all. The rules produce every verdict and every finding locally. Bedrock is
optional at two points, embeddings for the precedent list and a plain language
pass on remedy wording, and both degrade to a complete report that says what was
unavailable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .ingest import layout
from .ingest.extract import extract_facts
from .ingest.textract import TextractClient, hash_file
from .report import compose as compose_mod
from .report import render as render_mod
from .rules import engine


@dataclass
class ReviewResult:
    composed: Any
    text: str
    html: str
    facts: dict[str, Any] = field(default_factory=dict)
    subject: dict[str, Any] = field(default_factory=dict)
    offline: bool = True
    warnings: list[str] = field(default_factory=list)


def _load_graph_quietly():
    """The graph enriches findings with cross references. Absence is not fatal."""
    try:
        from .rules.graph import load_graph
        return load_graph()
    except Exception:  # noqa: BLE001
        return None


def find_local_pdf(permit_or_id: str) -> Path | None:
    """Look for a cached example PDF for a permit number or detail id."""
    examples = config.OUT_DIR / "examples"
    if not examples.exists():
        return None
    for pdf in sorted(examples.glob("*.pdf")):
        if permit_or_id in pdf.stem:
            return pdf
    return None


def s3_key_for_permit(permit_or_id: str, manifest: Path | None = None) -> str | None:
    """Find the first harvested document for a permit in the manifest."""
    manifest = Path(manifest or (config.OUT_DIR / "manifest_control.jsonl"))
    if not manifest.exists():
        return None
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if permit_or_id not in (
            str(record.get("detail_id")), str(record.get("permitNumber"))
        ):
            continue
        documents = record.get("documents") or []
        for document in documents:
            key = document.get("s3_key") or document.get("key")
            if key:
                return key
    return None


def analyze(
    pdf: Path | None = None,
    permit: str | None = None,
    manifest: Path | None = None,
    allow_network: bool = True,
    client: TextractClient | None = None,
):
    """Get a read Document plus a description of where it came from.

    Tries the offline cache before anything else, so the common demo path makes no
    network call and needs no credentials.

    subject describes the document and nothing else. It used to carry a source line
    naming the service and saying whether a cache was used, and for a harvested
    permit that line held the full S3 key. Every value in subject is rendered, so
    that put a storage path onto a projected screen and into any report a reviewer
    forwarded. It also read as a caveat: a cache hit is keyed by the SHA256 of the
    document, so it means this exact packet was analysed before, not that the
    result was staged for a demo. What a reviewer needs is which document produced
    the findings and how long it is. The offline guarantee is still real and is
    still what makes the demo survive a failed network, it just does not need
    narrating on screen, and the rule count line already says it in the right register.
    """
    client = client or TextractClient()
    subject: dict[str, Any] = {}

    if permit and pdf is None:
        pdf = find_local_pdf(permit)
        if pdf is not None:
            subject["permit_number"] = permit

    if pdf is not None:
        pdf = Path(pdf)
        subject["document"] = pdf.name
        doc_hash = hash_file(pdf)
        subject["document_hash"] = doc_hash
        analysis = client.cached_by_hash(doc_hash)
        if analysis is not None:
            return analysis, subject, True
        if not allow_network:
            raise RuntimeError(
                f"no cached analysis for {pdf.name} and network use was declined. "
                f"Run once with network access to populate the cache."
            )
        analysis = client.analyze_file(pdf)
        return analysis, subject, False

    if permit:
        key = s3_key_for_permit(permit, manifest)
        if key is None:
            raise RuntimeError(
                f"permit {permit} has no harvested document. Only 218 of the 1226 "
                f"approved permits carry one. Pass --pdf with a local file instead."
            )
        subject["permit_number"] = permit
        # The file name only. The bucket and the key stay out of subject, because
        # everything in subject is rendered, and a projected screen or a forwarded
        # report is the last place a storage path belongs.
        subject["document"] = key.rsplit("/", 1)[-1]
        cached = client.cached(key)
        if cached is not None:
            return cached, subject, True
        if not allow_network:
            raise RuntimeError(f"no cached analysis for {key} and network declined")
        analysis = client.analyze(key)
        return analysis, subject, False

    raise ValueError("pass either a pdf path or a permit number")


def permit_row(permit: str) -> dict | None:
    """Find a permit's CSV row, for coordinates. Returns None without the CSV.

    The CSV is gitignored and 45 MB, so its absence is normal rather than an
    error. Screening is skipped when it is not there.
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    if not config.PERMIT_CSV.exists():
        return None
    try:
        frame = pd.read_csv(config.PERMIT_CSV, dtype=str, low_memory=False)
    except Exception:  # noqa: BLE001
        return None
    subset = frame[frame["permitNumber"].astype(str) == str(permit)]
    if subset.empty:
        return None
    return subset.iloc[0].to_dict()


def screen_location(permit: str | None):
    """Geospatial screening for a permit, or None.

    Returns a Screening. A permit with no coordinates, which is about ten percent
    of them, yields a Screening with no point, which contributes no facts and so
    leaves any rule needing them UNKNOWN rather than passed.
    """
    if not permit:
        return None
    try:
        from . import geo
    except ImportError:
        return None
    row = permit_row(permit)
    if row is None:
        return None
    try:
        return geo.screen_permit(row)
    except Exception:  # noqa: BLE001 - screening is advisory, never fatal
        return None


def draw_location_map(permit: str | None, screening) -> str | None:
    """Draw the location map for a screened permit, or None.

    Returns a path relative to the report, so the HTML can reference it without an
    absolute path that breaks when the file is moved. Failure is never fatal: a
    report without a map is still a complete report.
    """
    if not permit or screening is None or screening.point is None:
        return None
    try:
        from . import maps
    except ImportError:
        return None
    try:
        result = maps.permit_map(
            permit, screening.point.lat, screening.point.lon,
            details=permit_row(permit),
        )
    except Exception:  # noqa: BLE001 - a figure is never worth failing a report
        return None
    if result is None:
        return None
    try:
        # Forward slashes, because this goes into an HTML src attribute and a
        # Windows backslash does not resolve there.
        return result.png.relative_to(config.OUT_DIR).as_posix()
    except ValueError:
        return result.png.as_posix()


def review(
    pdf: Path | None = None,
    permit: str | None = None,
    manifest: Path | None = None,
    allow_network: bool = True,
    with_precedents: bool = True,
    with_screening: bool = True,
    with_map: bool = True,
    rephrase: bool = False,
    client: TextractClient | None = None,
) -> ReviewResult:
    """Run the whole chain and return the rendered report."""
    warnings: list[str] = []

    analysis, subject, offline = analyze(
        pdf=pdf, permit=permit, manifest=manifest,
        allow_network=allow_network, client=client,
    )
    if not analysis.ok:
        raise RuntimeError(
            f"document analysis did not succeed: status {analysis.status} "
            f"{analysis.message or ''}".strip()
        )

    document = layout.parse_blocks(analysis.blocks)
    subject["pages"] = document.pages

    extraction = extract_facts(document)

    # Geospatial screening. The distance to mapped surface water is a measured
    # fact the engine can consume alongside anything read off the packet. It is
    # merged into the fact mapping rather than handled specially, so a rule that
    # wants it sees it exactly like any other value, and a permit with no
    # coordinates simply has no such fact.
    screening = None
    if with_screening:
        candidate = permit or subject.get("permit_number")
        if candidate is None and pdf is not None:
            # Example filenames carry the permit number: permit_281364_60839580.
            parts = Path(pdf).stem.split("_")
            candidate = parts[1] if len(parts) > 1 else None
        screening = screen_location(candidate)
        if screening is not None:
            extraction.facts.update(screening.facts())
            if with_map and screening.point is not None:
                figure = draw_location_map(candidate, screening)
                if figure is not None:
                    screening.figure_png = figure

    # The rules are the only thing that produces a verdict.
    report = engine.evaluate(extraction.facts)

    precedents = None
    if with_precedents:
        try:
            from .retrieval.search import search_for_facts
            precedents = search_for_facts(extraction.facts, k=5)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"precedent lookup unavailable: {exc}")

    composed = compose_mod.compose(
        report,
        extraction=extraction,
        graph=_load_graph_quietly(),
        precedents=precedents,
        screening=screening,
        subject=subject,
    )

    if rephrase:
        composed = compose_mod.rephrase_remedies(composed)

    return ReviewResult(
        composed=composed,
        text=render_mod.render_text(composed),
        html=render_mod.render_html(composed),
        facts=extraction.facts,
        subject=subject,
        offline=offline,
        warnings=warnings,
    )
