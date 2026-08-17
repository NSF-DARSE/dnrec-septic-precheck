"""Reading the permit CSV export and selecting which permits to harvest.

The CSV is the only bulk index of permits. It has one row per permit revision,
so several rows can share a detail page, and the detail page is what gets
fetched. Selection therefore always deduplicates on detail_id.

The mentor scoped the corpus to 2014 onward because 2014+ permits fall under the
current regulation and earlier ones are under superseded law. A naive year
filter silently discards rows with no parseable date, so select_permits reports
that count separately and only drops them when asked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import config
from .detail import ID_RE

YEAR_RE = re.compile(r"(\d{4})")

# Date columns in preference order. AppReceivedDate is the anchor because it is
# when the application entered the process; the others backfill it.
DATE_COLUMNS = ("AppReceivedDate", "ApprovedDate", "DeniedDate", "withdrawnDate")

# CSV column -> manifest field. Keeps manifest keys stable if the export renames.
PERMIT_FIELDS = {
    "permitNumber": "permitNumber",
    "permitStatus": "permitStatus",
    "County": "county",
    "TaxParcelNumbers": "taxParcel",
    "OwnerName": "ownerName",
    "Designer": "designer",
    "Contractor": "contractor",
    "SepticSystemType": "septicSystemType",
    "ConstructionType": "constructionType",
    "Flow Rate": "flowRate",
    "PerkRate": "perkRate",
    "SepticPropUseCode": "propUse",
    "AppReceivedDate": "appReceivedDate",
    "ApprovedDate": "approvedDate",
    "DeniedDate": "deniedDate",
    "withdrawnDate": "withdrawnDate",
}


def parse_year(value) -> int | None:
    """Last four digit run in a date string, or None.

    Handles the mixed formats in the export without asserting a single layout.
    """
    if value is None or isinstance(value, float):
        return None
    matches = YEAR_RE.findall(str(value))
    if not matches:
        return None
    year = int(matches[-1])
    return year if 1900 <= year <= 2100 else None


def permit_year(row: dict) -> int | None:
    for column in DATE_COLUMNS:
        year = parse_year(row.get(column))
        if year is not None:
            return year
    return None


@dataclass
class Selection:
    """Chosen permits plus the counts needed to justify the choice."""

    rows: list[dict] = field(default_factory=list)
    total_rows: int = 0
    matched_rows: int = 0
    unique_detail_pages: int = 0
    no_year: int = 0
    before_year_min: int = 0
    statuses: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"csv_rows={self.total_rows} matched_rows={self.matched_rows} "
            f"unique_detail_pages={self.unique_detail_pages} "
            f"selected={len(self.rows)} no_parseable_year={self.no_year} "
            f"older_than_cutoff={self.before_year_min}"
        )


def load_csv(path: Path | None = None) -> pd.DataFrame:
    path = Path(path or config.PERMIT_CSV)
    if not path.exists():
        raise FileNotFoundError(
            f"permit CSV not found at {path}. Set SEPTIC_PERMIT_CSV to override."
        )
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df["detail_id"] = df["URL for Permit Details"].str.extract(ID_RE, expand=False)
    return df[df["detail_id"].notna()].copy()


def select_permits(
    df: pd.DataFrame | None = None,
    statuses: list[str] | None = None,
    year_min: int | None = config.YEAR_MIN,
    keep_undated: bool = False,
    limit: int = 0,
) -> Selection:
    """Pick permits to harvest.

    statuses of None or ["ALL"] means every status. year_min of None disables the
    date cutoff. keep_undated decides the fate of rows with no parseable year:
    they are excluded by default, and counted either way so the number is never
    invisible.
    """
    if df is None:
        df = load_csv()

    sel = Selection(total_rows=len(df))

    if statuses and statuses != ["ALL"]:
        wanted = {s.strip().lower() for s in statuses}
        df = df[df["permitStatus"].str.strip().str.lower().isin(wanted)]
    sel.matched_rows = len(df)

    df = df.drop_duplicates("detail_id")
    sel.unique_detail_pages = len(df)

    rows: list[dict] = []
    for row in df.to_dict("records"):
        year = permit_year(row)
        if year is None:
            sel.no_year += 1
            if not keep_undated:
                continue
        elif year_min is not None and year < year_min:
            sel.before_year_min += 1
            continue

        record = {"detail_id": row["detail_id"], "year": year}
        for source, target in PERMIT_FIELDS.items():
            record[target] = row.get(source)
        rows.append(record)

    if limit:
        rows = rows[:limit]

    sel.rows = rows
    counts: dict[str, int] = {}
    for r in rows:
        key = r.get("permitStatus") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    sel.statuses = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return sel
