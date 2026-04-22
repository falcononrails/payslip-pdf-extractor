from __future__ import annotations

import csv
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
