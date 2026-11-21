"""Console report for the incident analysis (`scripts/analyze.py`).

Layout follows the briefing's expected output: a title block, a TOTAL RECORDS section, the invalid
breakdown, category / status / country breakdowns and the satisfaction index. Labels are aligned with
dot leaders. The report only ever contains counts, percentages and line numbers, never cell values.
"""

import sys

from .incident_analysis import (
    CATEGORIES,
    COUNTRIES,
    RULE_BY_KEY,
    RULES,
    SCORE_LABELS,
    SCORE_MAX,
    SCORE_MIN,
    STATUSES,
    AnalysisResult,
    percent,
)

WIDTH = 60
LEADER_COLUMN = 40  # the value column starts right after this many characters
LINES_TO_REVIEW = 10  # invalid records listed by line number in the console


def console_supports_unicode(stream=None) -> bool:
    """True when the console can print box-drawing characters and the em dash."""
    encoding = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        "├└─—".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _leader(label: str, value: str, branch: str = "") -> str:
    head = f"  {branch} {label} " if branch else f"{label} "
    return f"{head}{'.' * max(3, LEADER_COLUMN - len(head))} {value}"


def _branches(unicode_ok: bool) -> tuple[str, str]:
    return ("├─", "└─") if unicode_ok else ("|-", "`-")


def _tree(items: list[tuple[str, str]], unicode_ok: bool) -> list[str]:
    middle, last = _branches(unicode_ok)
    return [_leader(label, value, last if index == len(items) - 1 else middle) for index, (label, value) in enumerate(items)]


def _counted(count: int, total: int) -> str:
    return f"{count:>4}  ({percent(count, total):.1f}%)"


def render_report(result: AnalysisResult, unicode_ok: bool = True) -> str:
    """The full console report as text (ends with the closing separator line)."""
    dash = "—" if unicode_ok else "-"
    rule_line = "=" * WIDTH
    lines = [
        rule_line,
        f"  TRACKFLOW {dash} INCIDENT REPORT ANALYSIS",
        f"  Source file: {result.source_file or '(unnamed)'}",
        rule_line,
        "",
        _leader("TOTAL RECORDS IN FILE", str(result.total_records)),
        *_tree([("Valid records", str(result.valid_records)), ("Invalid / incomplete", str(result.invalid_records))], unicode_ok),
        "",
        "INVALID RECORDS BREAKDOWN",
    ]

    triggered = [(rule.label, str(result.rule_counts[rule.key])) for rule in RULES if result.rule_counts[rule.key]]
    if triggered:
        lines += _tree(triggered, unicode_ok)
        if sum(result.rule_counts.values()) > result.invalid_records:
            lines.append(f"  (a record can break several rules, so these add up to more than {result.invalid_records})")
        lines += ["", f"INVALID RECORDS TO REVIEW (first {min(LINES_TO_REVIEW, len(result.invalid_examples))} by line number)"]
        for line_number, keys in result.invalid_examples[:LINES_TO_REVIEW]:
            lines.append(f"  Line {line_number}: {', '.join(RULE_BY_KEY[key].label for key in keys)}")
        hidden = result.invalid_records - min(LINES_TO_REVIEW, len(result.invalid_examples))
        if hidden > 0:
            lines.append(f"  ... and {hidden} more invalid records")
    else:
        lines += _tree([("None", "0")], unicode_ok)

    for title, counts in (
        ("BREAKDOWN BY CATEGORY (valid records)", result.by_category),
        ("BREAKDOWN BY STATUS (valid records)", result.by_status),
        ("BREAKDOWN BY COUNTRY (valid records)", result.by_country),
    ):
        lines += ["", title, *_tree([(name, _counted(count, result.valid_records)) for name, count in counts.items()], unicode_ok)]

    lines += ["", "SATISFACTION INDEX (closed incidents)", f"  Scored incidents: {result.scored} of {result.closed_valid}"]
    average = result.average_score
    lines.append(f"  Average score: {average:.2f} / {SCORE_MAX:.2f}" if average is not None else "  Average score: n/a (no scored closed incidents)")
    lines += _tree(
        [(f"Score {score} ({SCORE_LABELS[score]})", str(result.score_counts[score])) for score in range(SCORE_MIN, SCORE_MAX + 1)],
        unicode_ok,
    )

    # Valentina's question: is satisfaction worse in the US or Spain, or in particular categories?
    for title, pairs in (
        ("SATISFACTION BY COUNTRY (closed incidents)", result.scores_by_country),
        ("SATISFACTION BY CATEGORY (closed incidents)", result.scores_by_category),
    ):
        rows = []
        for name, (scored, total) in pairs.items():
            shown = f"{total / scored:.2f} / {SCORE_MAX:.2f}" if scored else "n/a"
            rows.append((name, f"{shown}  ({scored} scored)"))
        lines += ["", title, *_tree(rows, unicode_ok)]

    if result.warnings:
        lines += ["", "NOTES", *[f"  - {warning}" for warning in result.warnings]]

    lines += ["", rule_line]
    return "\n".join(lines)


# Order of the sections, kept next to the renderer so tests can assert it.
SECTIONS = ("TOTAL RECORDS IN FILE", "INVALID RECORDS BREAKDOWN", "BREAKDOWN BY CATEGORY", "BREAKDOWN BY STATUS", "BREAKDOWN BY COUNTRY", "SATISFACTION INDEX")
__all__ = ["render_report", "console_supports_unicode", "SECTIONS", "CATEGORIES", "STATUSES", "COUNTRIES"]
