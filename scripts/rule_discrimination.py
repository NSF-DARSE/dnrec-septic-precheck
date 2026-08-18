"""Measure whether the rules separate denied applications from approved ones.

This runs the rule set over two groups of permits drawn from the CSV, the denied
and returned ones and the approved ones, and reports how often each rule trips in
each group. If the rules are picking up something real, they should trip more
often on the group that was actually refused.

Read this before believing any number it prints.

Until a person certifies a rule the engine returns UNKNOWN for it, so today this
harness reports almost everything as UNKNOWN. That is the expected result and not
a bug. The harness exists so that a real number is available the moment rules are
certified, rather than being written in a hurry afterwards.

Two things limit what this can ever show. Only 101 denied permits and 3 returned
ones exist from 2014 onward, so the negative group is small enough that a handful
of records moves any percentage several points. And the CSV carries only seven
structured columns, so the rules that need a measurement off a site plan cannot be
evaluated here at all. Those limits are printed with the results rather than left
for the reader to work out.

UNKNOWN is reported as its own category throughout. It is never folded into pass or
fail, because the three mean different things and collapsing them is how a harness
starts flattering the rules it is supposed to test.

Usage:
    python scripts/rule_discrimination.py
    python scripts/rule_discrimination.py --limit 100
    python scripts/rule_discrimination.py --assume-verified
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import _bootstrap  # noqa: F401

from septic import config
from septic.harvest import csv_index
from septic.rules import engine
from septic.rules.schema import Outcome

# ===========================================================================
# THE MAPPING SEAM
#
# Everything that translates a CSV column into a rule engine parameter lives in
# this one block. It is isolated and commented because it is the place where a
# silent mistake poisons every number this script prints, and a wrong mapping
# looks exactly like a working one: the rules still run, the counts still add up,
# and the answer is meaningless.
#
# Four hazards in this data, all handled below.
#
# 1. flowRate uses a period as a thousands separator in 16 records, so "2.475"
#    means 2,475 gallons per day and not 2.475. Read literally, a 2475 gallon
#    system becomes a 2.475 gallon one, which would fail the 240 gallon minimum
#    that it actually clears by a factor of ten.
#
# 2. propUse encodes the bedroom count rather than the use, as "3-bedroom". It is
#    the only source of bedroom count in the CSV, and it also establishes that the
#    property is residential.
#
# 3. septicSystemType uses display names that do not match the rule vocabulary,
#    so "Low Pressure Pipe" has to become "low pressure pipe" and the several
#    gravity variants have to collapse onto "gravity".
#
# 4. constructionType matters for correctness, not just description. Section
#    5.2.4.2.4.2 exempts replacement systems from the 20 inch limiting zone rule,
#    so a replacement must not be scored against it. The exemption cannot be
#    expressed in applies_to today, which is recorded in that rule's notes, so
#    this harness records the construction type and reports the affected subset
#    separately rather than pretending the distinction does not exist.
# ===========================================================================

# CSV septicSystemType -> the system_type vocabulary in rules_7101.yaml.
SYSTEM_TYPE_MAP = {
    "gravity": "gravity",
    "low pressure pipe": "low pressure pipe",
    "elevated mound": "sand mound",
    "alternative elevated sand mound": "sand mound",
    "pressure dose": "pressure dosed",
    "peat": "peat",
    "irrigation": "drip",
    "holding tank": "holding tank",
}

# constructionType values that mean this is not a new build. Section 5.2.4.2.4.2
# treats these differently and the rule set cannot yet express that.
REPLACEMENT_TYPES = {
    "replacement", "component replacement", "upgrade",
    "repair to existing system",
}

BEDROOM_RE = re.compile(r"^(\d+)\s*-?\s*bedroom", re.IGNORECASE)

# A flowRate written with a period and exactly three following digits is using
# the period as a thousands separator.
THOUSANDS_RE = re.compile(r"^(\d{1,3})\.(\d{3})$")


def parse_flow(raw) -> float | None:
    """Design flow in gallons per day, or None.

    Handles the thousands separator described in hazard 1 above. Anything that
    does not parse cleanly returns None so the rule reports UNKNOWN, rather than
    being coerced into a number that would be compared against a threshold.
    """
    if raw in (None, "", "nan"):
        return None
    text = str(raw).strip()
    if text.lower() == "nan":
        return None
    text = text.replace(",", "")
    match = THOUSANDS_RE.match(text)
    if match:
        return float(f"{match.group(1)}{match.group(2)}")
    try:
        return float(text)
    except ValueError:
        return None


def parse_number(raw) -> float | None:
    if raw in (None, "", "nan"):
        return None
    text = str(raw).strip().replace(",", "")
    if text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def facts_from_csv_row(row: dict) -> tuple[dict, dict]:
    """Map one CSV row onto rule engine parameters.

    Returns the fact mapping and a small record of what could not be mapped, so
    the report can say which parameters were absent by data limitation rather than
    by a failure to read them.

    A parameter is omitted entirely when the CSV cannot supply it. It is never
    defaulted, because the engine treats an absent parameter as UNKNOWN and a
    defaulted one as measured, and those produce different answers.
    """
    facts: dict = {}
    context: dict = {}

    # perkRate -> perc_rate, minutes per inch. Same quantity, same units.
    perc = parse_number(row.get("perkRate"))
    if perc is not None:
        facts["perc_rate"] = perc

    # flowRate -> design_flow, gallons per day. See hazard 1.
    flow = parse_flow(row.get("flowRate"))
    if flow is not None:
        facts["design_flow"] = flow
        # Section 5.0 scopes small systems to under 2500 gallons per day, so scale
        # follows from flow and is not a separate column.
        facts["system_scale"] = "small" if flow < 2500 else "large"

    # propUse -> use_type and bedrooms. See hazard 2.
    prop_use = str(row.get("propUse") or "").strip()
    bedroom_match = BEDROOM_RE.match(prop_use)
    if bedroom_match:
        bedrooms = int(bedroom_match.group(1))
        facts["use_type"] = "residential"
        facts["bedrooms"] = bedrooms
        if flow is not None and bedrooms:
            facts["design_flow_per_bedroom"] = round(flow / bedrooms, 2)
    elif prop_use.lower() in ("other", "", "nan"):
        # "Other" is not a use this rule set recognises. Left absent rather than
        # guessed: mapping it to residential would switch on the residential flow
        # rules for commercial properties.
        context["use_type_unmapped"] = prop_use

    # septicSystemType -> system_type. See hazard 3.
    raw_type = str(row.get("septicSystemType") or "").strip().lower()
    mapped_type = SYSTEM_TYPE_MAP.get(raw_type)
    if mapped_type:
        facts["system_type"] = mapped_type
    elif raw_type not in ("", "nan"):
        context["system_type_unmapped"] = raw_type

    # constructionType. Recorded for reporting, not fed to the engine, because no
    # rule takes it as a parameter yet. See hazard 4.
    construction = str(row.get("constructionType") or "").strip().lower()
    if construction not in ("", "nan"):
        context["construction_type"] = construction
        context["is_replacement"] = construction in REPLACEMENT_TYPES

    # county and taxParcel are carried for identification only. No rule uses
    # either, and inventing a geographic rule from them would not be traceable to
    # the regulation.
    if row.get("county"):
        context["county"] = str(row["county"]).strip()

    return facts, context


# Parameters no CSV column can supply. Listed explicitly so the output can
# distinguish "the rules could not run because nobody measured it" from "the
# rules could not run because the threshold is uncertified".
NOT_IN_CSV = (
    "dist_disposal_to_well",
    "dist_disposal_to_watercourse",
    "dist_disposal_to_property_line",
    "dist_disposal_to_escarpment",
    "dist_tank_to_well",
    "dist_tank_to_watercourse",
    "perc_test_holes",
    "limiting_zone_depth",
    "limiting_zone_below_trench_bottom",
    "disposal_slope",
    "absorption_type",
    "site_evaluation_report",
    "wells_within_150_feet_shown",
)

# ===========================================================================
# End of the mapping seam.
# ===========================================================================


@dataclass
class GroupResult:
    """Counts for one group of permits."""

    name: str
    statuses: list[str]
    records: int = 0
    mapped_records: int = 0
    tripped_any: int = 0
    tripped_evaluable: int = 0
    unknown_all: int = 0
    per_rule: dict[str, Counter] = field(default_factory=dict)
    unknown_reason: Counter = field(default_factory=Counter)
    replacements: int = 0
    fact_coverage: Counter = field(default_factory=Counter)

    def rate(self, count: int) -> str:
        if not self.records:
            return "n/a"
        return f"{100.0 * count / self.records:.1f}%"

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "statuses": self.statuses,
            "records": self.records,
            "mapped_records": self.mapped_records,
            "tripped_at_least_one_rule": self.tripped_any,
            "tripped_at_least_one_evaluable_rule": self.tripped_evaluable,
            "all_rules_unknown": self.unknown_all,
            "replacements": self.replacements,
            "per_rule": {
                rule_id: dict(counter) for rule_id, counter in self.per_rule.items()
            },
            "unknown_reason": dict(self.unknown_reason),
            "fact_coverage": dict(self.fact_coverage),
        }


def evaluable_rules(rules) -> tuple[list, list]:
    """Split rules into those the CSV can actually test and those it cannot.

    A rule whose parameter has no CSV column cannot produce a meaningful result
    here, and two of them produce a badly misleading one. SITE-001 and SITE-002
    use the present operator, and for a presence check an absent parameter is a
    FAIL rather than an UNKNOWN, which is right for a real packet where a missing
    site evaluation report is a genuine deficiency. Against the CSV it means every
    single record fails both rules because the export has no column for them, so
    the headline trip rate reads 100 percent in both groups and hides everything
    else. They are reported separately for that reason.
    """
    testable, untestable = [], []
    for rule in rules:
        if rule.parameter in NOT_IN_CSV:
            untestable.append(rule)
        else:
            testable.append(rule)
    return testable, untestable


def evaluate_group(name: str, statuses: list[str], df, rules, limit: int = 0
                   ) -> GroupResult:
    selection = csv_index.select_permits(df, statuses=statuses, year_min=config.YEAR_MIN)
    rows = selection.rows[:limit] if limit else selection.rows

    testable, _ = evaluable_rules(rules)
    testable_ids = {r.id for r in testable}

    result = GroupResult(name=name, statuses=statuses)
    for rule in rules:
        result.per_rule[rule.id] = Counter()

    for row in rows:
        result.records += 1
        facts, context = facts_from_csv_row(row)
        if facts:
            result.mapped_records += 1
        if context.get("is_replacement"):
            result.replacements += 1
        for parameter in facts:
            result.fact_coverage[parameter] += 1

        report = engine.evaluate(facts, rules)
        tripped = False
        tripped_testable = False
        all_unknown = True
        for evaluation in report.evaluations:
            outcome = evaluation.outcome
            rule = evaluation.rule

            # A PASS can mean two different things and they must not be pooled.
            # The engine reports a rule that does not apply as a PASS, which is
            # right for a verdict but wrong for this table: "no bed system, so the
            # bed slope rule is irrelevant" is not evidence the rule works. The
            # engine now carries that distinction on the evaluation itself, so it
            # is read here rather than recomputed. A second copy of the
            # applicability logic would eventually disagree with the engine and
            # this harness would silently measure the wrong thing.
            bucket = outcome.value
            if evaluation.is_not_applicable:
                bucket = "NOT_APPLICABLE"
            result.per_rule[rule.id][bucket] += 1

            if outcome is Outcome.FAIL:
                tripped = True
                all_unknown = False
                if rule.id in testable_ids:
                    tripped_testable = True
            elif evaluation.compared_a_value:
                all_unknown = False
            elif evaluation.is_not_applicable:
                # Out of scope, so it neither reached a decision nor failed to
                # read anything. Counted in its own bucket above and nowhere else.
                pass
            else:
                # Separate the two reasons a check did not run. They call for
                # different fixes: one needs a person, the other needs data.
                if not rule.verified:
                    result.unknown_reason["threshold not certified"] += 1
                elif rule.parameter in NOT_IN_CSV:
                    result.unknown_reason["parameter not in the CSV"] += 1
                elif rule.parameter not in facts:
                    result.unknown_reason["parameter missing from this record"] += 1
                else:
                    result.unknown_reason["value could not be compared"] += 1
        if tripped:
            result.tripped_any += 1
        if tripped_testable:
            result.tripped_evaluable += 1
        if all_unknown:
            result.unknown_all += 1

    return result


def render(groups: list[GroupResult], rules, assumed_verified: bool) -> str:
    L: list[str] = []
    add = L.append

    add("=" * 78)
    add("RULE DISCRIMINATION: DENIED AND RETURNED VERSUS APPROVED")
    add("=" * 78)
    add("")
    add(f"rules loaded              {len(rules)}")
    add(f"rules certified by a human {sum(1 for r in rules if r.verified)}")
    add(f"year cutoff               {config.YEAR_MIN} onward")
    add("")
    if assumed_verified:
        add("MODE: --assume-verified. Every rule was treated as certified for this")
        add("run only, in memory. NOTHING WAS WRITTEN and no rule was certified.")
        add("These numbers show what the rules would report if a person confirmed")
        add("them exactly as written. They are a projection, not a measurement, and")
        add("they must not be quoted as evidence the rules are correct.")
    else:
        add("MODE: as shipped. No rule has been certified, so the engine returns")
        add("UNKNOWN for every one of them and no rule can trip. That is the")
        add("expected result today and it is the interlock working, not a failure.")
        add("Run with --assume-verified to see the shape of the answer that becomes")
        add("available once rules are certified.")
    add("")

    testable, untestable = evaluable_rules(rules)

    add("-" * 78)
    add("GROUP TOTALS")
    add("-" * 78)
    add(f"{'group':<26}{'records':>9}{'mapped':>9}{'tripped':>10}{'all unknown':>14}")
    for g in groups:
        add(f"{g.name:<26}{g.records:>9}{g.mapped_records:>9}"
            f"{g.tripped_any:>10}{g.unknown_all:>14}")
    add("")
    for g in groups:
        add(f"{g.name}: {g.rate(g.tripped_any)} tripped at least one rule, "
            f"{g.rate(g.unknown_all)} came back entirely UNKNOWN")
    add("")

    if untestable:
        add("READ THIS BEFORE THE TRIP RATE ABOVE")
        add(f"  {len(untestable)} of {len(rules)} rules need a value no CSV column")
        add("  supplies, so they cannot be tested here at all:")
        for rule in untestable:
            add(f"    {rule.id}  needs {rule.parameter}")
        add("")
        add("  Two of them use the present operator, and for a presence check an")
        add("  absent value is a FAIL rather than an UNKNOWN. That is correct for a")
        add("  real packet, where a missing site evaluation report is a genuine")
        add("  deficiency, but against the CSV it means every record fails them")
        add("  because the export has no such column. That alone pins the trip rate")
        add("  above at 100 percent in both groups and tells you nothing.")
        add("")
        add(f"  Restricting to the {len(testable)} rules the CSV can actually test:")
        for g in groups:
            add(f"    {g.name:<24}{g.tripped_evaluable:>6} of {g.records} records "
                f"tripped  ({g.rate(g.tripped_evaluable)})")
        add("")

    if len(groups) == 2 and all(g.records for g in groups):
        negative, positive = groups[0], groups[1]
        neg_rate = negative.tripped_evaluable / negative.records
        pos_rate = positive.tripped_evaluable / positive.records
        add("SEPARATION, over the rules the CSV can test")
        if negative.tripped_evaluable == 0 and positive.tripped_evaluable == 0:
            add("  No testable rule tripped in either group, so there is nothing to")
            add("  compare.")
            if not assumed_verified:
                add("  With no rule certified this is exactly what should happen. It")
                add("  is the interlock working rather than a failure.")
            else:
                add("  Every rule was treated as certified for this run, so this says")
                add("  something about the data rather than about certification: the")
                add(f"  {len(testable)} rules the CSV can test are all satisfied by")
                add("  every record in both groups. DNREC does not record a permit as")
                add("  approved with a percolation rate the regulation forbids, and")
                add("  the flow figures clear their minimums comfortably, so these")
                add("  three rules cannot separate the groups no matter how well they")
                add("  are written. The rules that could separate them need a")
                add("  measurement off a site plan, which means Textract over the 218")
                add("  approved permits that carry a document and the harvested")
                add("  denied set. Until that runs, this question is open.")
        else:
            add(f"  denied and returned: {neg_rate * 100:.1f}% tripped")
            add(f"  approved:            {pos_rate * 100:.1f}% tripped")
            difference = (neg_rate - pos_rate) * 100
            add(f"  difference:          {difference:+.1f} points")
            add("")
            if difference <= 0:
                add("  The rules do not trip more often on the refused group, so on")
                add("  this evidence they are not separating the two. That may mean")
                add("  a rule is wrong, or that the deficiency which caused the")
                add("  refusal is simply not among the things these rules check.")
            else:
                add("  The rules trip more often on the refused group. Treat this as")
                add("  weak evidence: the refused group has 104 records, so a handful")
                add("  of them moves this figure by several points.")
        add("")

    add("-" * 78)
    add("PER RULE")
    add("-" * 78)
    add("Counts are records, not percentages, because the denied group is small")
    add("enough that percentages imply more precision than exists.")
    add("")
    add("F  failed, the rule applied and was not met")
    add("P  passed, the rule applied and was met")
    add("U  unknown, the check could not run")
    add("N  not applicable, applies_to excluded this record. Reported separately")
    add("   from P because a rule that did not apply is not a rule that worked.")
    add("")
    header = f"{'rule':<48}"
    for g in groups:
        header += f"{g.name[:18]:>20}"
    add(header)
    add(f"{'':<48}" + "".join(f"{'F / P / U / N':>20}" for _ in groups))
    for rule in rules:
        marker = " " if rule in testable else "*"
        line = f"{marker}{rule.id:<47}"
        for g in groups:
            counter = g.per_rule.get(rule.id, Counter())
            cell = (f"{counter.get('FAIL', 0)} / {counter.get('PASS', 0)} / "
                    f"{counter.get('UNKNOWN', 0)} / "
                    f"{counter.get('NOT_APPLICABLE', 0)}")
            line += f"{cell:>20}"
        add(line)
    add("")
    add("* rule needs a value no CSV column supplies, so its row is an artifact of")
    add("  the data source and not a measurement.")
    add("")

    add("-" * 78)
    add("WHY CHECKS DID NOT RUN")
    add("-" * 78)
    add("UNKNOWN is a category of its own here and is never counted as a pass.")
    add("")
    for g in groups:
        add(f"{g.name}:")
        total = sum(g.unknown_reason.values())
        for reason, count in g.unknown_reason.most_common():
            share = f"{100.0 * count / total:.1f}%" if total else "n/a"
            add(f"  {reason:<44}{count:>8}  {share:>7}")
        add("")

    add("-" * 78)
    add("WHICH PARAMETERS THE CSV COULD SUPPLY")
    add("-" * 78)
    add("The CSV has seven structured columns. Every rule needing a measurement")
    add("from a site plan is unevaluable here by construction, and needs Textract.")
    add("")
    for g in groups:
        add(f"{g.name} ({g.records} records):")
        for parameter, count in sorted(g.fact_coverage.items()):
            add(f"  {parameter:<40}{count:>8} records")
        add(f"  replacement or repair records: {g.replacements}")
        add("")
    add("parameters no CSV column can supply:")
    for parameter in NOT_IN_CSV:
        add(f"  {parameter}")
    add("")

    add("-" * 78)
    add("LIMITS OF THIS MEASUREMENT")
    add("-" * 78)
    add("1. The refused group is tiny. 101 denied and 3 returned permits exist")
    add("   from 2014 onward, against 1226 approved. A single record is roughly a")
    add("   point of movement in the refused group.")
    add("2. The final status is not the reason. DNREC returns an application, the")
    add("   applicant fixes it, and the resubmission is approved, so a permit can")
    add("   appear as approved having been returned twice. The return letters that")
    add("   say why are not published.")
    add("3. Section 5.2.4.2.4.2 exempts replacement systems from the 20 inch")
    add("   limiting zone rule, and applies_to cannot express that yet, so any")
    add("   count for SEP-002 over the replacement records is not meaningful.")
    add("4. Nothing here validates a threshold. A rule can separate the groups")
    add("   perfectly and still cite the wrong number, which is why certification")
    add("   is reading the regulation and not running this script.")
    add("")

    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rule_discrimination")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap records per group, for a quick check")
    ap.add_argument("--assume-verified", action="store_true",
                    help="treat every rule as certified for this run only, in "
                         "memory, to show the shape of the answer")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    rules = engine.load_rules()
    if args.assume_verified:
        rules = copy.deepcopy(rules)
        for rule in rules:
            rule.verified = True

    df = csv_index.load_csv()
    groups = [
        evaluate_group("denied and returned", ["Denied", "Application Returned"],
                       df, rules, args.limit),
        evaluate_group("approved", ["Approved"], df, rules, args.limit),
    ]

    text = render(groups, rules, args.assume_verified)
    print(text)

    out_dir = args.out or config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "rule_discrimination.txt"
    json_path = out_dir / "rule_discrimination.json"
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "year_min": config.YEAR_MIN,
                "rules": len(rules),
                "rules_certified": sum(1 for r in rules if r.verified),
                "assume_verified_mode": args.assume_verified,
                "groups": [g.to_json() for g in groups],
                "parameters_not_in_csv": list(NOT_IN_CSV),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {txt_path}")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
