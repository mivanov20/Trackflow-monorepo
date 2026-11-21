"""Validation and metrics for the after-sales incident file (`incidents.csv`).

Single source of truth for the analysis: `scripts/analyze.py` and the API endpoint
`POST /api/incidents/analyze` both call `analyze_csv`, so the numbers can never diverge.

Field names, allowed values and rules follow the TrackFlow briefing (CSV structure and
"Rules for Invalid Records"). Only the standard library is used, and the file is read row by
row, so a file of a million rows is analysed without loading it into memory.

Privacy: `customer_email` is only checked for an `@`. No cell value is ever stored in a result,
printed or exported; invalid records are identified by line number and rule only.
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, TextIO

from .incidents import CSV_CATEGORY_MAP, CSV_COUNTRY_BRANCH_MAP, CSV_STATUS_MAP

# --- CSV structure (briefing, "CSV Structure") ---------------------------------------

REQUIRED_COLUMNS = (
    "incident_id",
    "date",
    "country",
    "customer_type",
    "tracking_number",
    "carrier",
    "category",
    "description",
    "status",
    "customer_email",
)
SCORE_COLUMN = "satisfaction_score"  # optional in the file, but required for CLOSED records

# Same codes the incident manager already uses for seeding, so both stay in step with the briefing.
CATEGORIES = tuple(CSV_CATEGORY_MAP)  # LOST_PARCEL, DELAYED_DELIVERY, WRONG_ADDRESS, RETURN_REQUEST, DAMAGE
STATUSES = tuple(CSV_STATUS_MAP)  # OPEN, CLOSED, DISCARDED
COUNTRIES = tuple(CSV_COUNTRY_BRANCH_MAP)  # US, ES
CARRIERS: dict[str, tuple[str, ...]] = {
    "US": ("UPS", "FEDEX", "DHL_US"),
    "ES": ("MRW", "SEUR", "DHL_ES", "LOCAL_ES"),
}

MIN_TRACKING_LENGTH = 8
MIN_DESCRIPTION_LENGTH = 5
SCORE_MIN, SCORE_MAX = 1, 5
SCORE_LABELS = {1: "Very dissatisfied", 2: "Dissatisfied", 3: "Neutral", 4: "Satisfied", 5: "Very satisfied"}

# Invalid records listed individually (by line number) in a result; the counts are always complete.
MAX_INVALID_EXAMPLES = 100


@dataclass(frozen=True)
class Rule:
    """One way a record can be invalid."""

    key: str
    label: str
    description: str


# Display order: the five rules of the briefing's expected output first, then the rest.
RULES: tuple[Rule, ...] = (
    Rule("invalid_tracking_number", "Invalid tracking number", f"tracking_number is empty or has fewer than {MIN_TRACKING_LENGTH} characters"),
    Rule("invalid_carrier", "Carrier/country mismatch", "carrier is empty, unknown, or does not operate in the declared country"),
    Rule("invalid_category", "Invalid or missing category", "category is empty or not one of the five valid categories"),
    Rule("invalid_email", "Invalid or missing email", "customer_email is empty or does not contain @"),
    Rule("closed_without_score", "Closed incident, no score", "status is CLOSED and there is no satisfaction_score"),
    Rule("invalid_country", "Invalid or missing country", "country is empty or not US / ES"),
    Rule("invalid_description", "Invalid or empty description", f"description is empty or has fewer than {MIN_DESCRIPTION_LENGTH} characters"),
    Rule("score_out_of_range", "Satisfaction score out of range", f"satisfaction_score is present but not an integer from {SCORE_MIN} to {SCORE_MAX}"),
    Rule("invalid_status", "Invalid or missing status", "status is empty or not OPEN / CLOSED / DISCARDED"),
    Rule("missing_required_field", "Missing required field", "incident_id, date or customer_type is empty"),
)
RULE_KEYS = tuple(rule.key for rule in RULES)
RULE_BY_KEY = {rule.key: rule for rule in RULES}


class InvalidFileError(ValueError):
    """The file cannot be analysed at all. The message is written for the user and never contains file content."""


# --- row validation --------------------------------------------------------------------


def _clean(value: Any) -> str:
    """Cell text without surrounding spaces; short rows give None for missing cells."""
    return value.strip() if isinstance(value, str) else ""


def parse_score(text: str) -> int | None:
    """The score as an integer, or None if the text is not an integer."""
    try:
        return int(text)
    except ValueError:
        return None


def check_row(row: dict[str, Any]) -> list[str]:
    """Keys of every rule the record breaks, in display order (empty list = valid record)."""
    broken: set[str] = set()

    country = _clean(row.get("country"))
    if country not in CARRIERS:
        broken.add("invalid_country")

    carrier = _clean(row.get("carrier"))
    # With an invalid country there is nothing to match against: only reject carriers no country uses.
    allowed_carriers = CARRIERS.get(country) or tuple(name for names in CARRIERS.values() for name in names)
    if carrier not in allowed_carriers:
        broken.add("invalid_carrier")

    if len(_clean(row.get("tracking_number"))) < MIN_TRACKING_LENGTH:
        broken.add("invalid_tracking_number")
    if _clean(row.get("category")) not in CATEGORIES:
        broken.add("invalid_category")
    if len(_clean(row.get("description"))) < MIN_DESCRIPTION_LENGTH:
        broken.add("invalid_description")
    if "@" not in _clean(row.get("customer_email")):
        broken.add("invalid_email")

    status = _clean(row.get("status"))
    if status not in STATUSES:
        broken.add("invalid_status")

    score_text = _clean(row.get(SCORE_COLUMN))
    if score_text:
        score = parse_score(score_text)
        if score is None or not SCORE_MIN <= score <= SCORE_MAX:
            broken.add("score_out_of_range")
    elif status == "CLOSED":
        broken.add("closed_without_score")

    if any(not _clean(row.get(name)) for name in ("incident_id", "date", "customer_type")):
        broken.add("missing_required_field")

    return [key for key in RULE_KEYS if key in broken]


# --- numbers ---------------------------------------------------------------------------


def percent(count: int, total: int) -> float:
    """`count` as a percentage of `total`, to one decimal (half up). 0.0 when there is nothing to divide."""
    if total == 0:
        return 0.0
    return float((Decimal(count) * 100 / Decimal(total)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def average(total: int, count: int) -> float | None:
    """`total / count` to two decimals (half up), or None when there are no values."""
    if count == 0:
        return None
    return float((Decimal(total) / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --- result ----------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """Everything the analysis produces. Only counts and line numbers, never cell values."""

    source_file: str
    total_records: int
    valid_records: int
    invalid_records: int
    rule_counts: dict[str, int]
    invalid_examples: list[tuple[int, tuple[str, ...]]]
    by_category: dict[str, int]
    by_status: dict[str, int]
    by_country: dict[str, int]
    closed_valid: int
    score_counts: dict[int, int]
    scores_by_country: dict[str, tuple[int, int]]  # country -> (scored incidents, sum of scores)
    scores_by_category: dict[str, tuple[int, int]]
    warnings: list[str] = field(default_factory=list)
    analyzed_at: str = ""

    @property
    def scored(self) -> int:
        """Valid closed incidents that have a score."""
        return sum(self.score_counts.values())

    @property
    def average_score(self) -> float | None:
        return average(sum(score * count for score, count in self.score_counts.items()), self.scored)

    def breakdown(self, counts: dict[str, int]) -> list[dict[str, Any]]:
        """`[{value, count, percent}]` with the percentage taken over the valid records."""
        return [{"value": value, "count": count, "percent": percent(count, self.valid_records)} for value, count in counts.items()]

    def satisfaction_breakdown(self, pairs: dict[str, tuple[int, int]]) -> list[dict[str, Any]]:
        return [{"value": value, "scored": scored, "average": average(total, scored)} for value, (scored, total) in pairs.items()]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary (the API response)."""
        return {
            "source_file": self.source_file,
            "analyzed_at": self.analyzed_at,
            "total_records": self.total_records,
            "valid_records": self.valid_records,
            "invalid_records": self.invalid_records,
            "invalid_breakdown": [
                {"rule": rule.key, "label": rule.label, "description": rule.description, "count": self.rule_counts[rule.key]}
                for rule in RULES
            ],
            "invalid_examples": [{"line": line, "rules": list(keys)} for line, keys in self.invalid_examples],
            "invalid_examples_truncated": self.invalid_records > len(self.invalid_examples),
            "by_category": self.breakdown(self.by_category),
            "by_status": self.breakdown(self.by_status),
            "by_country": self.breakdown(self.by_country),
            "satisfaction": {
                "closed_valid": self.closed_valid,
                "scored": self.scored,
                "average": self.average_score,
                "distribution": [
                    {"score": score, "label": SCORE_LABELS[score], "count": self.score_counts[score]}
                    for score in range(SCORE_MIN, SCORE_MAX + 1)
                ],
                "by_country": self.satisfaction_breakdown(self.scores_by_country),
                "by_category": self.satisfaction_breakdown(self.scores_by_category),
            },
            "warnings": list(self.warnings),
        }

    def to_csv_rows(self) -> list[tuple[str, str]]:
        """One `(metric, value)` row per metric, for `results.csv` and the export endpoint."""
        rows: list[tuple[str, str]] = [
            ("total_records", str(self.total_records)),
            ("valid_records", str(self.valid_records)),
            ("invalid_records", str(self.invalid_records)),
        ]
        rows += [(f"invalid.{rule.key}", str(self.rule_counts[rule.key])) for rule in RULES]
        for prefix, counts in (("category", self.by_category), ("status", self.by_status), ("country", self.by_country)):
            for value, count in counts.items():
                rows.append((f"{prefix}.{value}", str(count)))
                rows.append((f"{prefix}.{value}.percent", f"{percent(count, self.valid_records):.1f}"))
        rows += [
            ("satisfaction.closed_valid", str(self.closed_valid)),
            ("satisfaction.scored", str(self.scored)),
            ("satisfaction.average", _format_average(self.average_score)),
        ]
        rows += [(f"satisfaction.score_{score}", str(self.score_counts[score])) for score in range(SCORE_MIN, SCORE_MAX + 1)]
        for prefix, pairs in (("country", self.scores_by_country), ("category", self.scores_by_category)):
            for value, (scored, total) in pairs.items():
                rows.append((f"satisfaction.{prefix}.{value}.scored", str(scored)))
                rows.append((f"satisfaction.{prefix}.{value}.average", _format_average(average(total, scored))))
        return rows


def _format_average(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def write_results_csv(result: AnalysisResult, handle: TextIO) -> int:
    """Write `metric,value` (header plus one row per metric) to `handle`; returns the number of metric rows."""
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(["metric", "value"])
    rows = result.to_csv_rows()
    writer.writerows(rows)
    return len(rows)


def results_csv_text(result: AnalysisResult) -> str:
    """The results CSV as a string."""
    buffer = io.StringIO()
    write_results_csv(result, buffer)
    return buffer.getvalue()


# --- analysis --------------------------------------------------------------------------


def _read_rows(reader: "csv.DictReader[str]") -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield `(first line of the record, row)`, turning low-level read failures into InvalidFileError."""
    previous_end = reader.line_num
    iterator = iter(reader)
    while True:
        try:
            row = next(iterator)
        except StopIteration:
            return
        except UnicodeDecodeError as error:
            raise InvalidFileError("The file is not valid UTF-8 text. Export it again as UTF-8 and try again.") from error
        except csv.Error as error:
            raise InvalidFileError("The file is not a well-formed CSV (a quote or a very long cell could not be read).") from error
        yield previous_end + 1, row
        previous_end = reader.line_num


def analyze_csv(handle: TextIO, source_name: str = "") -> AnalysisResult:
    """Validate every record and compute the metrics. `handle` is an open text stream (UTF-8, `newline=""`).

    Raises InvalidFileError when the file cannot be analysed (empty, wrong separator or missing columns,
    not UTF-8, malformed CSV, no data rows). Records that break a rule never raise: they are counted.
    """
    reader = csv.DictReader(handle)
    try:
        header = reader.fieldnames
    except UnicodeDecodeError as error:
        raise InvalidFileError("The file is not valid UTF-8 text. Export it again as UTF-8 and try again.") from error
    except csv.Error as error:
        raise InvalidFileError("The file is not a well-formed CSV.") from error
    if not header:
        raise InvalidFileError("The file is empty: it has no header row.")
    reader.fieldnames = [name.strip().lower() for name in header]

    missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
    if missing:
        hint = " Check that the separator is a comma." if len(reader.fieldnames) == 1 else ""
        raise InvalidFileError(f"The file is missing required column(s): {', '.join(missing)}.{hint}")

    warnings: list[str] = []
    if SCORE_COLUMN not in reader.fieldnames:
        warnings.append(f"The file has no '{SCORE_COLUMN}' column, so every CLOSED record counts as incomplete.")

    total = valid = 0
    rule_counts = dict.fromkeys(RULE_KEYS, 0)
    invalid_examples: list[tuple[int, tuple[str, ...]]] = []
    by_category = dict.fromkeys(CATEGORIES, 0)
    by_status = dict.fromkeys(STATUSES, 0)
    by_country = dict.fromkeys(COUNTRIES, 0)
    closed_valid = 0
    score_counts = dict.fromkeys(range(SCORE_MIN, SCORE_MAX + 1), 0)
    scores_by_country = {country: (0, 0) for country in COUNTRIES}
    scores_by_category = {category: (0, 0) for category in CATEGORIES}

    for line, row in _read_rows(reader):
        total += 1
        broken = check_row(row)
        if broken:
            for key in broken:
                rule_counts[key] += 1
            if len(invalid_examples) < MAX_INVALID_EXAMPLES:
                invalid_examples.append((line, tuple(broken)))
            continue

        valid += 1
        category, status, country = _clean(row["category"]), _clean(row["status"]), _clean(row["country"])
        by_category[category] += 1
        by_status[status] += 1
        by_country[country] += 1
        if status == "CLOSED":
            closed_valid += 1
            score = int(_clean(row.get(SCORE_COLUMN)))  # valid closed records always have a score in range
            score_counts[score] += 1
            for pairs, key in ((scores_by_country, country), (scores_by_category, category)):
                scored, total_score = pairs[key]
                pairs[key] = (scored + 1, total_score + score)

    if total == 0:
        raise InvalidFileError("The file has a header but no data rows.")

    return AnalysisResult(
        source_file=source_name,
        total_records=total,
        valid_records=valid,
        invalid_records=total - valid,
        rule_counts=rule_counts,
        invalid_examples=invalid_examples,
        by_category=by_category,
        by_status=by_status,
        by_country=by_country,
        closed_valid=closed_valid,
        score_counts=score_counts,
        scores_by_country=scores_by_country,
        scores_by_category=scores_by_category,
        warnings=warnings,
        analyzed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def analyze_path(path: str, source_name: str | None = None) -> AnalysisResult:
    """Analyse the CSV at `path` (read as UTF-8, with or without a byte-order mark)."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return analyze_csv(handle, source_name if source_name is not None else str(path).replace("\\", "/").rsplit("/", 1)[-1])
