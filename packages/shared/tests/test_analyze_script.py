"""scripts/analyze.py ejecutado como proceso real: salida por consola, exportación y errores."""

import csv
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from synthetic_incidents import COLUMNS, write_csv

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "analyze.py"

# Lo que el briefing dice que debe aparecer: (etiqueta, número, porcentaje opcional).
EXPECTED_LINES = [
    ("TOTAL RECORDS IN FILE", "100", None),
    ("Valid records", "95", None),
    ("Invalid / incomplete", "5", None),
    ("Invalid tracking number", "1", None),
    ("Carrier/country mismatch", "1", None),
    ("Invalid or missing category", "1", None),
    ("Invalid or missing email", "1", None),
    ("Closed incident, no score", "1", None),
    ("LOST_PARCEL", "14", "14.7"),
    ("DELAYED_DELIVERY", "38", "40.0"),
    ("WRONG_ADDRESS", "19", "20.0"),
    ("RETURN_REQUEST", "17", "17.9"),
    ("DAMAGE", "7", "7.4"),
    ("OPEN", "29", "30.5"),
    ("CLOSED", "52", "54.7"),
    ("DISCARDED", "14", "14.7"),
    ("US", "50", "52.6"),
    ("ES", "45", "47.4"),
    ("Score 1 (Very dissatisfied)", "6", None),
    ("Score 2 (Dissatisfied)", "11", None),
    ("Score 3 (Neutral)", "15", None),
    ("Score 4 (Satisfied)", "14", None),
    ("Score 5 (Very satisfied)", "6", None),
]


@pytest.fixture
def sample(tmp_path):
    return write_csv(tmp_path / "incidents-trackflow.csv")


def run(*args, cwd, stdin="", encoding="utf-8"):
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        input=stdin, capture_output=True, text=True, encoding=encoding, errors="replace", cwd=cwd, env=env, timeout=60,
    )


def test_the_console_shows_every_value_of_the_briefing(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="n\n")
    assert done.returncode == 0, done.stderr
    for label, count, pct in EXPECTED_LINES:
        pattern = rf"{re.escape(label)} \.+ +{count}\b" + (rf"\s+\({re.escape(pct)}%\)" if pct else "")
        assert re.search(pattern, done.stdout), f"falta: {label} {count} {pct}"
    assert "Source file: incidents-trackflow.csv" in done.stdout
    assert "Scored incidents: 52 of 52" in done.stdout and "Average score: 3.06 / 5.00" in done.stdout


def test_the_report_has_every_required_section_in_order(sample, tmp_path):
    out = run(sample, cwd=tmp_path, stdin="n\n").stdout
    positions = [out.index(title) for title in (
        "TOTAL RECORDS IN FILE", "INVALID RECORDS BREAKDOWN", "BREAKDOWN BY CATEGORY (valid records)",
        "BREAKDOWN BY STATUS (valid records)", "BREAKDOWN BY COUNTRY (valid records)", "SATISFACTION INDEX (closed incidents)",
    )]
    assert positions == sorted(positions)
    assert "=" * 60 in out and out.rstrip().endswith("Export results to CSV? [y / n]:")


def test_the_report_lists_which_lines_to_review_without_their_content(sample, tmp_path):
    out = run(sample, cwd=tmp_path, stdin="n\n").stdout
    assert "Line 8: Invalid tracking number" in out and "Line 91: Closed incident, no score" in out
    assert "TRK" not in out and "SHORT1" not in out


def test_answering_n_creates_no_file(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="n\n")
    assert done.returncode == 0 and not (tmp_path / "results.csv").exists()


def test_answering_y_writes_results_csv_with_one_row_per_metric(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="y\n")
    assert done.returncode == 0 and "Results saved to results.csv" in done.stdout
    rows = list(csv.reader((tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()))
    assert rows[0] == ["metric", "value"] and all(len(row) == 2 for row in rows)
    values = {metric: value for metric, value in rows[1:]}
    assert len(values) == len(rows) - 1  # una fila por métrica, sin repeticiones
    assert values["total_records"] == "100" and values["valid_records"] == "95" and values["invalid_records"] == "5"
    assert values["category.DELAYED_DELIVERY"] == "38" and values["status.CLOSED"] == "52" and values["country.ES"] == "45"
    assert values["satisfaction.average"] == "3.06" and values["satisfaction.scored"] == "52"


def test_a_closed_input_counts_as_no(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="")
    assert done.returncode == 0 and not (tmp_path / "results.csv").exists()


def test_an_unclear_answer_is_asked_again(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="maybe\nyes\n")
    assert "Please answer y or n." in done.stdout and (tmp_path / "results.csv").exists()


def test_export_flag_skips_the_question(sample, tmp_path):
    done = run(sample, "--export", cwd=tmp_path)
    assert done.returncode == 0 and "Export results to CSV?" not in done.stdout and (tmp_path / "results.csv").exists()


def test_it_runs_from_the_scripts_folder_too(sample):
    done = run(sample, cwd=SCRIPT.parent, stdin="n\n")
    assert done.returncode == 0 and "TOTAL RECORDS IN FILE" in done.stdout


def test_a_console_without_unicode_gets_an_ascii_report(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="n\n", encoding="ascii")
    assert done.returncode == 0, done.stderr
    assert "|-" in done.stdout and "`-" in done.stdout and "TRACKFLOW - INCIDENT REPORT ANALYSIS" in done.stdout


def test_a_missing_file_exits_1_with_a_message(tmp_path):
    done = run(tmp_path / "nope.csv", cwd=tmp_path)
    assert done.returncode == 1 and "File not found" in done.stderr and done.stdout == ""


def test_no_argument_prints_the_usage(tmp_path):
    done = run(cwd=tmp_path)
    assert done.returncode == 2 and "usage: analyze.py" in done.stderr


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),
        (",".join(COLUMNS) + "\n", "no data rows"),
        ("incident_id,date\nTRF-000001,2025-03-01\n", "missing required column"),
        (";".join(COLUMNS) + "\nx\n", "separator is a comma"),
    ],
)
def test_unusable_files_exit_1_without_a_report(tmp_path, content, message):
    bad = tmp_path / "bad.csv"
    bad.write_text(content, encoding="utf-8")
    done = run(bad, cwd=tmp_path)
    assert done.returncode == 1 and message in done.stderr and done.stdout == ""


def test_a_file_that_is_not_utf8_exits_1(tmp_path):
    bad = tmp_path / "latin1.csv"
    bad.write_bytes((",".join(COLUMNS) + "\n" + "x,y,z,w,v,u,t,Caf\xe9,s,secret@customer.com,1\n").encode("latin-1"))
    done = run(bad, cwd=tmp_path)
    assert done.returncode == 1 and "UTF-8" in done.stderr
    assert "secret@customer.com" not in done.stdout + done.stderr


def test_an_unwritable_results_file_exits_1_with_a_message(sample, tmp_path):
    (tmp_path / "results.csv").mkdir()  # no se puede escribir un archivo donde hay una carpeta
    done = run(sample, cwd=tmp_path, stdin="y\n")
    assert done.returncode == 1 and "Could not write results.csv" in done.stderr


def test_no_customer_email_appears_in_any_output(sample, tmp_path):
    done = run(sample, cwd=tmp_path, stdin="y\n")
    exported = (tmp_path / "results.csv").read_text(encoding="utf-8")
    for text in (done.stdout, done.stderr, exported):
        assert "@" not in text and "example.com" not in text and "customer0" not in text
