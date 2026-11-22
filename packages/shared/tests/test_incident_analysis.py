"""Análisis del archivo de incidencias: reglas de registros inválidos, métricas y errores de archivo."""

import csv
import io
import json
from pathlib import Path

import pytest
from synthetic_incidents import COLUMNS, EXPECTED, build_rows, write_csv

from trackflow_shared import incident_analysis as ia
from trackflow_shared.incident_analysis import InvalidFileError, analyze_csv, check_row

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_SAMPLE = REPO_ROOT / "data" / "eval" / "incidents-synthetic.csv"


def good_row(**overrides):
    row = {
        "incident_id": "TRF-000001", "date": "2025-03-01", "country": "US", "customer_type": "B2C",
        "tracking_number": "TRK123456789", "carrier": "UPS", "category": "LOST_PARCEL",
        "description": "Parcel never arrived.", "status": "OPEN", "customer_email": "someone@example.com",
        "satisfaction_score": "",
    }
    row.update(overrides)
    return row


def csv_text(rows, columns=COLUMNS):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def analyze_rows(rows, columns=COLUMNS):
    return analyze_csv(io.StringIO(csv_text(rows, columns)), "test.csv")


# --- los números del briefing -------------------------------------------------------


@pytest.fixture(scope="module")
def sample_result(tmp_path_factory):
    path = write_csv(tmp_path_factory.mktemp("sample") / "incidents.csv")
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return analyze_csv(handle, "incidents-trackflow.csv")


def test_totals_match_the_briefing(sample_result):
    assert (sample_result.total_records, sample_result.valid_records, sample_result.invalid_records) == (100, 95, 5)


def test_invalid_breakdown_matches_the_briefing(sample_result):
    triggered = {key: count for key, count in sample_result.rule_counts.items() if count}
    assert triggered == EXPECTED["invalid_rules"]
    assert all(count == 0 for key, count in sample_result.rule_counts.items() if key not in EXPECTED["invalid_rules"])


def test_category_status_and_country_breakdowns_match_the_briefing(sample_result):
    assert sample_result.by_category == EXPECTED["category"]
    assert sample_result.by_status == EXPECTED["status"]
    assert sample_result.by_country == EXPECTED["country"]
    for name, counts in (("category", sample_result.by_category), ("status", sample_result.by_status), ("country", sample_result.by_country)):
        percents = {item["value"]: item["percent"] for item in sample_result.breakdown(counts)}
        assert percents == EXPECTED[f"{name}_percent"]


def test_satisfaction_matches_the_briefing(sample_result):
    assert sample_result.closed_valid == 52 and sample_result.scored == EXPECTED["scored"]
    assert sample_result.score_counts == EXPECTED["scores"]
    assert sample_result.average_score == EXPECTED["average"] == 3.06


def test_the_committed_sample_is_the_generated_one(tmp_path):
    assert COMMITTED_SAMPLE.read_text(encoding="utf-8") == write_csv(tmp_path / "again.csv").read_text(encoding="utf-8")


def test_the_allowed_sets_follow_the_briefing():
    assert ia.CATEGORIES == ("LOST_PARCEL", "DELAYED_DELIVERY", "WRONG_ADDRESS", "RETURN_REQUEST", "DAMAGE")
    assert ia.STATUSES == ("OPEN", "CLOSED", "DISCARDED")
    assert ia.COUNTRIES == ("US", "ES")
    assert ia.CARRIERS == {"US": ("UPS", "FEDEX", "DHL_US"), "ES": ("MRW", "SEUR", "DHL_ES", "LOCAL_ES")}
    assert ia.REQUIRED_COLUMNS == tuple(COLUMNS[:-1]) and ia.SCORE_COLUMN == "satisfaction_score"


# --- una prueba por regla ---------------------------------------------------------


def test_a_good_record_breaks_no_rule():
    assert check_row(good_row()) == []
    assert check_row(good_row(status="CLOSED", satisfaction_score="4")) == []
    assert check_row(good_row(country="ES", carrier="LOCAL_ES")) == []


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"country": ""}, "invalid_country"),
        ({"country": "FR"}, "invalid_country"),
        ({"country": "us"}, "invalid_country"),
        ({"carrier": ""}, "invalid_carrier"),
        ({"carrier": "PIGEON"}, "invalid_carrier"),
        ({"carrier": "MRW"}, "invalid_carrier"),  # solo opera en España
        ({"country": "ES", "carrier": "UPS"}, "invalid_carrier"),
        ({"tracking_number": ""}, "invalid_tracking_number"),
        ({"tracking_number": "1234567"}, "invalid_tracking_number"),  # 7 caracteres
        ({"category": ""}, "invalid_category"),
        ({"category": "FIRE"}, "invalid_category"),
        ({"category": "lost_parcel"}, "invalid_category"),
        ({"description": ""}, "invalid_description"),
        ({"description": "four"}, "invalid_description"),
        ({"customer_email": ""}, "invalid_email"),
        ({"customer_email": "no-at-sign.example.com"}, "invalid_email"),
        ({"status": "CLOSED", "satisfaction_score": ""}, "closed_without_score"),
        ({"satisfaction_score": "0"}, "score_out_of_range"),
        ({"satisfaction_score": "6"}, "score_out_of_range"),
        ({"satisfaction_score": "-1"}, "score_out_of_range"),
        ({"satisfaction_score": "3.5"}, "score_out_of_range"),
        ({"satisfaction_score": "great"}, "score_out_of_range"),
        ({"status": ""}, "invalid_status"),
        ({"status": "PENDING"}, "invalid_status"),
        ({"status": "closed"}, "invalid_status"),
        ({"incident_id": ""}, "missing_required_field"),
        ({"date": ""}, "missing_required_field"),
        ({"customer_type": ""}, "missing_required_field"),
    ],
)
def test_each_rule_flags_exactly_its_record(overrides, rule):
    assert check_row(good_row(**overrides)) == [rule]


def test_boundaries_are_inclusive():
    assert check_row(good_row(tracking_number="12345678")) == []  # 8 caracteres
    assert check_row(good_row(description="12345")) == []  # 5 caracteres
    for score in ("1", "5"):
        assert check_row(good_row(status="CLOSED", satisfaction_score=score)) == []


def test_surrounding_spaces_are_ignored():
    assert check_row(good_row(country=" US ", carrier=" UPS ", category=" DAMAGE ", status=" OPEN ", satisfaction_score="  ")) == []
    assert check_row(good_row(status="CLOSED", satisfaction_score=" 4 ")) == []
    assert check_row(good_row(tracking_number="   1234567   ")) == ["invalid_tracking_number"]  # los espacios no cuentan


def test_a_score_on_an_open_record_must_still_be_valid():
    assert check_row(good_row(status="OPEN", satisfaction_score="4")) == []
    assert check_row(good_row(status="OPEN", satisfaction_score="9")) == ["score_out_of_range"]


def test_a_closed_record_with_a_bad_score_breaks_only_the_range_rule():
    assert check_row(good_row(status="CLOSED", satisfaction_score="9")) == ["score_out_of_range"]


def test_an_unknown_country_does_not_also_blame_a_real_carrier():
    assert check_row(good_row(country="FR", carrier="UPS")) == ["invalid_country"]
    assert check_row(good_row(country="FR", carrier="PIGEON")) == ["invalid_carrier", "invalid_country"]  # orden de visualización de RULES


def test_a_record_can_break_several_rules_and_counts_once_as_invalid():
    result = analyze_rows([good_row(tracking_number="x", category="FIRE", customer_email="nope"), good_row()])
    assert result.invalid_records == 1 and result.valid_records == 1
    assert [key for key, count in result.rule_counts.items() if count] == ["invalid_tracking_number", "invalid_category", "invalid_email"]
    assert sum(result.rule_counts.values()) == 3


def test_short_rows_and_extra_cells_do_not_crash():
    text = ",".join(COLUMNS) + "\nTRF-000001,2025-03-01,US\n" + ",".join(good_row().values()) + ",extra-cell\n"
    result = analyze_csv(io.StringIO(text))
    assert (result.total_records, result.valid_records, result.invalid_records) == (2, 1, 1)


def test_blank_lines_are_not_records():
    text = csv_text([good_row()]) + "\n\n" + csv_text([good_row()]).split("\n", 1)[1]
    assert analyze_csv(io.StringIO(text)).total_records == 2


def test_header_names_are_trimmed_and_case_insensitive():
    header = " Incident_ID , DATE,country,customer_type,tracking_number,carrier,category,description,status,customer_email,Satisfaction_Score"
    text = header + "\n" + ",".join(good_row().values()) + "\n"
    assert analyze_csv(io.StringIO(text)).valid_records == 1


# --- satisfacción por país y categoría (nota de Valentina) --------------------------------


def test_satisfaction_by_country_and_category():
    rows = [
        good_row(country="US", carrier="UPS", category="DAMAGE", status="CLOSED", satisfaction_score="2"),
        good_row(country="US", carrier="UPS", category="DAMAGE", status="CLOSED", satisfaction_score="3"),
        good_row(country="ES", carrier="MRW", category="LOST_PARCEL", status="CLOSED", satisfaction_score="5"),
        good_row(country="ES", carrier="SEUR", category="DAMAGE", status="OPEN"),
    ]
    result = analyze_rows(rows)
    assert result.scores_by_country == {"US": (2, 5), "ES": (1, 5)}
    assert result.scores_by_category["DAMAGE"] == (2, 5) and result.scores_by_category["LOST_PARCEL"] == (1, 5)
    by_country = {item["value"]: item["average"] for item in result.satisfaction_breakdown(result.scores_by_country)}
    assert by_country == {"US": 2.5, "ES": 5.0}
    assert result.satisfaction_breakdown({"X": (0, 0)})[0]["average"] is None


def test_rounding_is_half_up():
    assert ia.percent(1, 8) == 12.5 and ia.percent(1, 3) == 33.3 and ia.percent(0, 0) == 0.0
    assert ia.average(25, 8) == 3.13  # 3.125 -> 3.13 (round() de Python daría 3.12)
    assert ia.average(0, 0) is None


def test_no_scored_closed_records_gives_no_average():
    result = analyze_rows([good_row(status="OPEN")])
    assert result.scored == 0 and result.average_score is None
    assert dict(result.to_csv_rows())["satisfaction.average"] == ""


# --- errores de archivo -----------------------------------------------------------------


def test_an_empty_file_is_rejected():
    with pytest.raises(InvalidFileError, match="empty"):
        analyze_csv(io.StringIO(""))


def test_a_header_without_rows_is_rejected():
    with pytest.raises(InvalidFileError, match="no data rows"):
        analyze_csv(io.StringIO(",".join(COLUMNS) + "\n"))


def test_missing_columns_are_named():
    with pytest.raises(InvalidFileError, match="carrier, customer_email"):
        analyze_rows([good_row()], [c for c in COLUMNS if c not in ("carrier", "customer_email")])


def test_a_semicolon_file_gets_a_separator_hint():
    with pytest.raises(InvalidFileError, match="separator is a comma"):
        analyze_csv(io.StringIO(";".join(COLUMNS) + "\n" + ";".join(good_row().values()) + "\n"))


def test_a_missing_score_column_is_allowed_but_warned_about():
    columns = [c for c in COLUMNS if c != "satisfaction_score"]
    result = analyze_rows([good_row(status="CLOSED"), good_row(status="OPEN")], columns)
    assert result.valid_records == 1 and result.rule_counts["closed_without_score"] == 1
    assert any("satisfaction_score" in warning for warning in result.warnings)


def test_a_file_that_is_not_utf8_is_rejected():
    raw = (",".join(COLUMNS) + "\n" + ",".join(good_row(description="Caf\xe9 broken").values()) + "\n").encode("latin-1")
    with pytest.raises(InvalidFileError, match="UTF-8"):
        analyze_csv(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", newline=""))


def test_a_binary_file_is_rejected_without_a_traceback():
    with pytest.raises(InvalidFileError):
        analyze_csv(io.TextIOWrapper(io.BytesIO(b"\x00\x01\x02\xff\xfe" * 100), encoding="utf-8", newline=""))


def test_a_byte_order_mark_is_accepted(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(csv_text([good_row()]), encoding="utf-8-sig")
    assert ia.analyze_path(str(path)).valid_records == 1


def test_a_cell_over_the_csv_limit_is_a_clean_error():
    big = good_row(description="x" * (csv.field_size_limit() + 10))
    with pytest.raises(InvalidFileError, match="well-formed"):
        analyze_rows([big])


# --- números de línea y escala ------------------------------------------------------------


def test_invalid_examples_carry_line_numbers_including_multiline_cells():
    text = csv_text([good_row()])  # línea 2
    text += csv_text([good_row(description="two\nlines here")]).split("\n", 1)[1]  # líneas 3-4 (celda con salto)
    text += csv_text([good_row(category="FIRE")]).split("\n", 1)[1]  # línea 5
    result = analyze_csv(io.StringIO(text))
    assert result.invalid_examples == [(5, ("invalid_category",))]
    assert result.valid_records == 2


def test_examples_are_capped_but_counts_stay_complete():
    result = analyze_rows([good_row(category="FIRE")] * (ia.MAX_INVALID_EXAMPLES + 25))
    assert result.invalid_records == ia.MAX_INVALID_EXAMPLES + 25
    assert len(result.invalid_examples) == ia.MAX_INVALID_EXAMPLES
    assert result.rule_counts["invalid_category"] == ia.MAX_INVALID_EXAMPLES + 25
    assert result.to_dict()["invalid_examples_truncated"] is True


def test_a_large_file_is_read_as_a_stream():
    header = ",".join(COLUMNS) + "\n"
    line = ",".join(good_row(status="CLOSED", satisfaction_score="4").values()) + "\n"
    rows = 120_000

    class Stream(io.TextIOBase):
        """Entrega el archivo línea a línea sin tenerlo nunca entero en memoria."""

        def __init__(self):
            self._remaining = rows
            self._sent_header = False

        def readable(self):
            return True

        def __iter__(self):
            return self

        def __next__(self):
            if not self._sent_header:
                self._sent_header = True
                return header
            if self._remaining == 0:
                raise StopIteration
            self._remaining -= 1
            return line

    result = analyze_csv(Stream(), "big.csv")
    assert result.total_records == rows and result.valid_records == rows
    assert result.average_score == 4.0 and result.score_counts[4] == rows


# --- exportación y privacidad -------------------------------------------------------------


def test_the_results_csv_has_one_row_per_metric(sample_result):
    text = ia.results_csv_text(sample_result)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["metric", "value"]
    metrics = [row[0] for row in rows[1:]]
    assert len(metrics) == len(set(metrics)) and all(len(row) == 2 for row in rows)
    values = {row[0]: row[1] for row in rows[1:]}
    assert values["total_records"] == "100" and values["valid_records"] == "95" and values["invalid_records"] == "5"
    assert values["invalid.invalid_tracking_number"] == "1" and values["invalid.invalid_country"] == "0"
    assert values["category.DELAYED_DELIVERY"] == "38" and values["category.DELAYED_DELIVERY.percent"] == "40.0"
    assert values["status.CLOSED"] == "52" and values["country.US"] == "50"
    assert values["satisfaction.average"] == "3.06" and values["satisfaction.score_3"] == "15"
    assert values["satisfaction.scored"] == "52" and values["satisfaction.closed_valid"] == "52"


def test_write_results_csv_reports_the_row_count(sample_result):
    buffer = io.StringIO()
    count = ia.write_results_csv(sample_result, buffer)
    assert count == len(buffer.getvalue().strip().split("\n")) - 1


def test_no_customer_email_ever_reaches_a_result_or_an_export(sample_result):
    everything = " ".join(
        [json.dumps(sample_result.to_dict()), ia.results_csv_text(sample_result), repr(sample_result), str(sample_result.to_dict())]
    )
    for fragment in ("@example.com", "customer0", "customer1", "example.com", "TRK0000"):
        assert fragment not in everything


def test_file_errors_never_echo_file_content():
    secret = "very.private@customer.com"
    for text in (f"{secret}\n{secret}\n", f"a;b;c\n{secret};1;2\n"):
        with pytest.raises(InvalidFileError) as caught:
            analyze_csv(io.StringIO(text))
        assert secret not in str(caught.value)


def test_to_dict_shape_for_the_api(sample_result):
    body = sample_result.to_dict()
    assert body["source_file"] == "incidents-trackflow.csv" and body["analyzed_at"]
    assert [item["rule"] for item in body["invalid_breakdown"]] == list(ia.RULE_KEYS)
    assert {item["rule"]: item["count"] for item in body["invalid_breakdown"] if item["count"]} == EXPECTED["invalid_rules"]
    assert [item["value"] for item in body["by_category"]] == list(ia.CATEGORIES)
    assert body["satisfaction"]["average"] == 3.06 and body["satisfaction"]["scored"] == 52
    assert [item["score"] for item in body["satisfaction"]["distribution"]] == [1, 2, 3, 4, 5]
    assert body["invalid_examples_truncated"] is False and len(body["invalid_examples"]) == 5
    json.dumps(body)  # serializable


def test_rows_of_the_synthetic_file_are_what_the_briefing_describes():
    rows = build_rows()
    assert len(rows) == 100 and len({row["incident_id"] for row in rows}) == 100
    assert all(row["customer_email"].endswith("@example.com") or "@" not in row["customer_email"] for row in rows)
