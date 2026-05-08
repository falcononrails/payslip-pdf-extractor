from __future__ import annotations

import csv
import logging
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from payslip_extractor.extractor import run_extraction


def test_run_extraction_separate_outputs_one_pdf_per_number(tmp_path) -> None:
    pdf_file = tmp_path / "payroll.pdf"
    _write_pdf(
        pdf_file,
        [
            "Bulletin page 1 matricule 123",
            "Bulletin page 2 unrelated 91234",
            "Bulletin page 3 CNSS 456 and matricule 123",
        ],
    )
    numbers_file = _write_numbers_csv(tmp_path, ["123", "456"])
    output_dir = tmp_path / "out"

    summary = run_extraction([pdf_file], numbers_file, output_dir, "separate")

    assert summary.numbers_count == 2
    assert summary.matched_numbers_count == 2
    assert summary.matched_pages_count == 2
    assert sorted(path.name for path in summary.output_files) == ["123.pdf", "456.pdf"]
    assert len(PdfReader(output_dir / "123.pdf").pages) == 2
    assert len(PdfReader(output_dir / "456.pdf").pages) == 1

    audit_rows = _read_audit(summary.audit_csv)
    matched_rows = [row for row in audit_rows if row["status"] == "matched"]
    assert len(matched_rows) == 3
    assert {row["page_number"] for row in matched_rows} == {"1", "3"}


def test_run_extraction_merged_keeps_page_once_for_multiple_numbers(tmp_path) -> None:
    pdf_file = tmp_path / "payroll.pdf"
    _write_pdf(
        pdf_file,
        [
            "Bulletin page 1 matricule 123",
            "Bulletin page 2 CNSS 456 and matricule 123",
            "Bulletin page 3 unrelated",
        ],
    )
    numbers_file = _write_numbers_csv(tmp_path, ["123", "456"])
    output_dir = tmp_path / "out"

    summary = run_extraction([pdf_file], numbers_file, output_dir, "merged")

    assert [path.name for path in summary.output_files] == ["matched_pages.pdf"]
    assert summary.matched_pages_count == 2
    assert len(PdfReader(output_dir / "matched_pages.pdf").pages) == 2

    audit_rows = _read_audit(summary.audit_csv)
    matched_rows = [row for row in audit_rows if row["status"] == "matched"]
    assert len(matched_rows) == 2
    assert matched_rows[1]["matched_numbers"] == "123;456"


def test_run_extraction_writes_no_match_row(tmp_path) -> None:
    pdf_file = tmp_path / "payroll.pdf"
    _write_pdf(pdf_file, ["No matching employee here"])
    numbers_file = _write_numbers_csv(tmp_path, ["123"])
    output_dir = tmp_path / "out"

    summary = run_extraction([pdf_file], numbers_file, output_dir, "merged")

    assert summary.output_files == ()
    audit_rows = _read_audit(summary.audit_csv)
    assert audit_rows[0]["status"] == "no_match"


def test_run_extraction_with_alphanumeric_identifiers(tmp_path) -> None:
    pdf_file = tmp_path / "payroll.pdf"
    _write_pdf(
        pdf_file,
        [
            "Employee 456A payslip",
            "Employee B789 payslip",
            "Unrelated page 12345",
        ],
    )
    numbers_file = _write_numbers_csv(tmp_path, ["456A", "B789"])
    output_dir = tmp_path / "out"

    summary = run_extraction([pdf_file], numbers_file, output_dir, "separate")

    assert summary.numbers_count == 2
    assert summary.matched_numbers_count == 2
    assert summary.matched_pages_count == 2
    assert sorted(path.name for path in summary.output_files) == ["456A.pdf", "B789.pdf"]


def test_run_extraction_sorts_payslip_pages_by_period(tmp_path) -> None:
    pdf_file = tmp_path / "payroll.pdf"
    _write_pdf(
        pdf_file,
        [
            "Employee 123 bulletin de paie 09/2026",
            "Employee 123 bulletin de paie aout 2026",
            "Employee 123 bulletin de paie 2026-07",
        ],
    )
    numbers_file = _write_numbers_csv(tmp_path, ["123"])
    output_dir = tmp_path / "out"

    run_extraction([pdf_file], numbers_file, output_dir, "separate")

    assert _pdf_page_texts(output_dir / "123.pdf") == [
        "Employee 123 bulletin de paie 2026-07",
        "Employee 123 bulletin de paie aout 2026",
        "Employee 123 bulletin de paie 09/2026",
    ]


def test_run_extraction_parallel_scans_multiple_pdfs(tmp_path, caplog, monkeypatch) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _write_pdf(first, ["Employee 123 bulletin 01/2026"])
    _write_pdf(second, ["Employee 456 bulletin 02/2026"])
    numbers_file = _write_numbers_csv(tmp_path, ["123", "456"])
    output_dir = tmp_path / "out"
    monkeypatch.setenv("PAYSLIP_EXTRACTOR_WORKERS", "2")

    with caplog.at_level(logging.INFO, logger="payslip_extractor"):
        summary = run_extraction([first, second], numbers_file, output_dir, "separate")

    assert summary.matched_pages_count == 2
    log_messages = [rec.message for rec in caplog.records]
    assert any("with up to 2 worker(s)" in message for message in log_messages)
    assert any("Parallel scan progress" in message for message in log_messages)


def test_large_pdf_logging_and_extraction(tmp_path, caplog) -> None:
    page_texts = []
    for i in range(500):
        if i == 10:
            page_texts.append(f"Employee CNSS 123A payslip for January")
        elif i == 250:
            page_texts.append(f"Employee matricule B789 February bulletin")
        elif i == 499:
            page_texts.append(f"Employee identifier XYZ999 and also CNSS 123A")
        else:
            page_texts.append(f"Page {i} no relevant data here unrelated text")

    pdf_file = tmp_path / "large.pdf"
    _write_pdf(pdf_file, page_texts)

    numbers_file = _write_numbers_csv(tmp_path, ["123A", "B789", "XYZ999"])
    output_dir = tmp_path / "out"

    with caplog.at_level(logging.INFO, logger="payslip_extractor"):
        summary = run_extraction([pdf_file], numbers_file, output_dir, "separate")

    assert summary.numbers_count == 3
    assert summary.matched_numbers_count == 3
    assert summary.matched_pages_count == 3

    assert len(PdfReader(output_dir / "123A.pdf").pages) == 2
    assert len(PdfReader(output_dir / "B789.pdf").pages) == 1
    assert len(PdfReader(output_dir / "XYZ999.pdf").pages) == 1

    log_messages = [rec.message for rec in caplog.records]

    scanning_start = [m for m in log_messages if "scanning..." in m]
    assert len(scanning_start) >= 1, f"Expected 'scanning...' log, got: {log_messages}"

    page_logs = [m for m in log_messages if "page" in m and "/" in m and "matched page(s)" in m]
    assert 10 <= len(page_logs) < 30, f"Expected throttled page progress logs, got {len(page_logs)}"

    done_logs = [m for m in log_messages if "done" in m]
    assert len(done_logs) >= 1, f"Expected 'done' log, got: {log_messages}"

    finished_logs = [m for m in log_messages if "Finished scanning" in m]
    assert len(finished_logs) == 1, f"Expected exactly one 'Finished scanning' log"


def _write_pdf(path: Path, page_texts: list[str]) -> None:
    pdf = canvas.Canvas(str(path))
    for text in page_texts:
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()


def _write_numbers_csv(tmp_path: Path, numbers: list[str]) -> Path:
    numbers_file = tmp_path / "numbers.csv"
    numbers_file.write_text("\n".join(numbers), encoding="utf-8")
    return numbers_file


def _read_audit(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _pdf_page_texts(path: Path) -> list[str]:
    reader = PdfReader(path)
    return [(page.extract_text() or "").strip() for page in reader.pages]
