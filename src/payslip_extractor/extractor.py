from __future__ import annotations

import csv
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from pypdf import PdfReader, PdfWriter

from payslip_extractor.matching import find_matching_numbers
from payslip_extractor.models import AuditRow, ExtractionSummary, PageMatch
from payslip_extractor.numbers import read_numbers
from payslip_extractor.pdf_text import iter_page_texts

OutputMode = Literal["separate", "merged"]
ProgressCallback = Callable[[str], None]

AUDIT_FIELDNAMES = [
    "status",
    "source_pdf",
    "page_number",
    "matched_numbers",
    "output_pdf",
    "message",
]


class ExtractionError(RuntimeError):
    """Raised when extraction cannot be completed."""


def run_extraction(
    pdf_paths: Iterable[Path | str],
    numbers_file: Path | str,
    output_dir: Path | str,
    mode: OutputMode,
    progress: ProgressCallback | None = None,
) -> ExtractionSummary:
    pdfs = [Path(path).expanduser().resolve() for path in pdf_paths]
    if not pdfs:
        raise ExtractionError("At least one PDF file is required.")

    if mode not in {"separate", "merged"}:
        raise ExtractionError("Output mode must be either 'separate' or 'merged'.")

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    numbers = read_numbers(Path(numbers_file).expanduser().resolve())
    matches, audit_rows = scan_pdfs(pdfs, numbers, progress=progress)

    output_files: list[Path] = []
    if matches:
        with PdfReaderCache() as reader_cache:
            if mode == "merged":
                merged_pdf = output_path / "matched_pages.pdf"
                _write_matches_pdf(merged_pdf, matches, reader_cache)
                output_files.append(merged_pdf)
                audit_rows.extend(_matched_audit_rows(matches, merged_pdf))
            else:
                output_files.extend(
                    _write_separate_outputs(output_path, numbers, matches, reader_cache, audit_rows)
                )

    audit_csv = output_path / "audit.csv"
    write_audit_csv(audit_csv, audit_rows)

    matched_numbers = {number for match in matches for number in match.matched_numbers}
    return ExtractionSummary(
        numbers_count=len(numbers),
        matched_numbers_count=len(matched_numbers),
        matched_pages_count=len(matches),
        output_files=tuple(output_files),
        audit_csv=audit_csv,
    )


def scan_pdfs(
    pdf_paths: Iterable[Path],
    numbers: list[str],
    progress: ProgressCallback | None = None,
) -> tuple[list[PageMatch], list[AuditRow]]:
    matches: list[PageMatch] = []
    audit_rows: list[AuditRow] = []
    number_set = set(numbers)

    for pdf_path in pdf_paths:
        if progress:
            progress(f"Scanning {pdf_path.name}...")

        found_in_pdf = False
        try:
            for page_index, text in iter_page_texts(pdf_path):
                page_matches = find_matching_numbers(text, numbers, number_set)
                if page_matches:
                    found_in_pdf = True
                    matches.append(PageMatch(pdf_path, page_index, page_matches))
        except Exception as exc:
            audit_rows.append(
                AuditRow(
                    status="error",
                    source_pdf=pdf_path,
                    page_number="",
                    matched_numbers="",
                    output_pdf="",
                    message=str(exc),
                )
            )
            continue

        if not found_in_pdf:
            audit_rows.append(
                AuditRow(
                    status="no_match",
                    source_pdf=pdf_path,
                    page_number="",
                    matched_numbers="",
                    output_pdf="",
                    message="No target number found",
                )
            )

    return matches, audit_rows


def write_audit_csv(path: Path, rows: Iterable[AuditRow]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=AUDIT_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_row())


def _write_separate_outputs(
    output_dir: Path,
    numbers: list[str],
    matches: list[PageMatch],
    reader_cache: "PdfReaderCache",
    audit_rows: list[AuditRow],
) -> list[Path]:
    matches_by_number: dict[str, list[PageMatch]] = defaultdict(list)
    for match in matches:
        for number in match.matched_numbers:
            matches_by_number[number].append(match)

    output_files: list[Path] = []
    for number in numbers:
        number_matches = matches_by_number.get(number)
        if not number_matches:
            continue

        output_pdf = output_dir / f"{_safe_filename(number)}.pdf"
        _write_matches_pdf(output_pdf, number_matches, reader_cache)
        output_files.append(output_pdf)
        audit_rows.extend(_matched_audit_rows(number_matches, output_pdf, only_number=number))

    return output_files


def _write_matches_pdf(
    output_pdf: Path,
    matches: list[PageMatch],
    reader_cache: "PdfReaderCache",
) -> None:
    writer = PdfWriter()
    for match in matches:
        reader = reader_cache.get(match.source_pdf)
        writer.add_page(reader.pages[match.page_index])

    with output_pdf.open("wb") as output_file:
        writer.write(output_file)


def _matched_audit_rows(
    matches: list[PageMatch],
    output_pdf: Path,
    only_number: str | None = None,
) -> list[AuditRow]:
    rows: list[AuditRow] = []
    for match in matches:
        matched_numbers = (only_number,) if only_number else match.matched_numbers
        rows.append(
            AuditRow(
                status="matched",
                source_pdf=match.source_pdf,
                page_number=match.page_number,
                matched_numbers=matched_numbers,
                output_pdf=output_pdf,
            )
        )
    return rows


def _safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return name or "number"


class PdfReaderCache:
    def __init__(self) -> None:
        self._handles: dict[Path, object] = {}
        self._readers: dict[Path, PdfReader] = {}

    def __enter__(self) -> "PdfReaderCache":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def get(self, path: Path) -> PdfReader:
        if path not in self._readers:
            handle = path.open("rb")
            self._handles[path] = handle
            self._readers[path] = PdfReader(handle, strict=False)
        return self._readers[path]

    def close(self) -> None:
        for handle in self._handles.values():
            close = getattr(handle, "close", None)
            if callable(close):
                close()
        self._handles.clear()
        self._readers.clear()
