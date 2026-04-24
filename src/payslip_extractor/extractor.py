from __future__ import annotations

import csv
import logging
import multiprocessing
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from pypdf import PdfReader, PdfWriter

from payslip_extractor.matching import find_matching_numbers
from payslip_extractor.models import AuditRow, ExtractionSummary, PageMatch
from payslip_extractor.numbers import read_numbers
from payslip_extractor.pdf_text import get_page_count, iter_page_texts

OutputMode = Literal["separate", "merged"]
ProgressCallback = Callable[[str], None]

logger = logging.getLogger("payslip_extractor")

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


def _format_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:
        return ""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins}m"


def _scan_pdf_worker(args: tuple[str, list[str], frozenset[str]]) -> tuple[str, list[tuple[int, tuple[str, ...]]], list[dict[str, str]], int]:
    pdf_path_str, numbers, number_set = args
    pdf_path = Path(pdf_path_str)
    number_list = numbers
    number_set_local = set(number_set)
    matches: list[tuple[int, tuple[str, ...]]] = []
    audit_rows: list[dict[str, str]] = []
    total_pages = 0

    try:
        for page_index, text in iter_page_texts(pdf_path):
            total_pages += 1
            page_matches = find_matching_numbers(text, number_list, number_set_local)
            if page_matches:
                matches.append((page_index, page_matches))
    except Exception as exc:
        audit_rows.append({
            "status": "error",
            "source_pdf": str(pdf_path),
            "page_number": "",
            "matched_numbers": "",
            "output_pdf": "",
            "message": str(exc),
        })
        return str(pdf_path), matches, audit_rows, total_pages

    if not matches:
        audit_rows.append({
            "status": "no_match",
            "source_pdf": str(pdf_path),
            "page_number": "",
            "matched_numbers": "",
            "output_pdf": "",
            "message": "No target identifier found",
        })

    return str(pdf_path), matches, audit_rows, total_pages


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

    numbers_file_path = Path(numbers_file).expanduser().resolve()
    logger.info("Loading identifiers from %s...", numbers_file_path.name)
    numbers = read_numbers(numbers_file_path)
    logger.info("Found %d identifier(s) to search for.", len(numbers))

    matches, audit_rows = scan_pdfs(pdfs, numbers, progress=progress)

    matched_numbers_set = {number for match in matches for number in match.matched_numbers}
    logger.info(
        "Scan complete. %d/%d identifier(s) matched across %d page(s).",
        len(matched_numbers_set),
        len(numbers),
        len(matches),
    )

    output_files: list[Path] = []
    if matches:
        logger.info("Writing output PDFs to %s...", output_path)
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
        logger.info("Done. Wrote %d output file(s).", len(output_files))
    else:
        logger.info("No matching pages found. No output PDFs created.")

    audit_csv = output_path / "audit.csv"
    write_audit_csv(audit_csv, audit_rows)

    return ExtractionSummary(
        numbers_count=len(numbers),
        matched_numbers_count=len(matched_numbers_set),
        matched_pages_count=len(matches),
        output_files=tuple(output_files),
        audit_csv=audit_csv,
    )


def _get_total_pages(pdf_path: Path) -> int:
    try:
        return get_page_count(pdf_path)
    except Exception:
        return 0


def scan_pdfs(
    pdf_paths: Iterable[Path],
    numbers: list[str],
    progress: ProgressCallback | None = None,
) -> tuple[list[PageMatch], list[AuditRow]]:
    all_matches: list[PageMatch] = []
    all_audit_rows: list[AuditRow] = []
    number_set = set(numbers)

    pdf_list = list(pdf_paths)
    total_pdfs = len(pdf_list)

    pdf_page_counts = {p: _get_total_pages(p) for p in pdf_list}
    total_pages_all = sum(pdf_page_counts.values())

    if total_pdfs > 1:
        return _scan_pdfs_parallel(pdf_list, numbers, number_set, pdf_page_counts, total_pages_all, progress)

    pdf_path = pdf_list[0]
    return _scan_single_pdf(pdf_path, numbers, number_set, pdf_page_counts, total_pages_all, 1, 1, progress)


def _scan_pdfs_parallel(
    pdf_list: list[Path],
    numbers: list[str],
    number_set: set[str],
    pdf_page_counts: dict[Path, int],
    total_pages_all: int,
    progress: ProgressCallback | None,
) -> tuple[list[PageMatch], list[AuditRow]]:
    total_pdfs = len(pdf_list)
    logger.info("Scanning %d PDF file(s), %d total pages (parallel)...", total_pdfs, total_pages_all)

    work_items = [
        (str(p), numbers, frozenset(number_set))
        for p in pdf_list
    ]

    overall_start = time.monotonic()
    all_matches: list[PageMatch] = []
    all_audit_rows: list[AuditRow] = []

    n_workers = min(len(pdf_list), multiprocessing.cpu_count())
    with multiprocessing.Pool(processes=n_workers) as pool:
        completed = 0
        for pdf_path_str, page_matches_raw, audit_raw, pages_in_pdf in pool.map(_scan_pdf_worker, work_items):
            completed += 1
            pdf_path = Path(pdf_path_str)
            total_pages = pdf_page_counts.get(pdf_path, pages_in_pdf)

            for page_idx, matched_nums in page_matches_raw:
                all_matches.append(PageMatch(pdf_path, page_idx, matched_nums))

            for row_dict in audit_raw:
                source = Path(row_dict["source_pdf"]) if row_dict["source_pdf"] else pdf_path
                all_audit_rows.append(AuditRow(
                    status=row_dict["status"],
                    source_pdf=source,
                    page_number=row_dict["page_number"],
                    matched_numbers=row_dict["matched_numbers"] if isinstance(row_dict["matched_numbers"], str) else tuple(row_dict["matched_numbers"].split(";")) if row_dict["matched_numbers"] else (),
                    output_pdf=row_dict["output_pdf"],
                    message=row_dict["message"],
                ))

            elapsed = time.monotonic() - overall_start
            scanned_so_far = sum(pdf_page_counts[p] for p in pdf_list[:completed])
            match_count = sum(1 for m in all_matches if m.source_pdf == pdf_path)
            remaining = total_pages_all - scanned_so_far
            eta_display = ""
            if elapsed > 0 and remaining > 0:
                rate = scanned_so_far / elapsed
                if rate > 0:
                    eta_str = _format_eta(remaining / rate)
                    if eta_str:
                        eta_display = f" (ETA: ~{eta_str})"

            status = f"{match_count} match(es)" if match_count else "no matches"
            msg = f"[{completed}/{total_pdfs}] {pdf_path.name} ({total_pages} pages) - done, {status}{eta_display}"
            logger.info(msg)
            if progress:
                progress(msg)

        overall_elapsed = time.monotonic() - overall_start
        logger.info(
            "Finished scanning %d pages in %s.",
            total_pages_all,
            _format_eta(overall_elapsed) or f"{overall_elapsed:.1f}s",
        )

    return all_matches, all_audit_rows


def _scan_single_pdf(
    pdf_path: Path,
    numbers: list[str],
    number_set: set[str],
    pdf_page_counts: dict[Path, int],
    total_pages_all: int,
    pdf_index: int,
    total_pdfs: int,
    progress: ProgressCallback | None,
) -> tuple[list[PageMatch], list[AuditRow]]:
    matches: list[PageMatch] = []
    audit_rows: list[AuditRow] = []
    total_pages = pdf_page_counts.get(pdf_path, 0)

    msg = f"[{pdf_index}/{total_pdfs}] {pdf_path.name} ({total_pages} pages) - scanning..."
    logger.info(msg)

    overall_start = time.monotonic()
    pages_scanned = 0
    last_log_time = overall_start

    try:
        for page_index, text in iter_page_texts(pdf_path):
            page_matches = find_matching_numbers(text, numbers, number_set)
            if page_matches:
                matches.append(PageMatch(pdf_path, page_index, page_matches))

            pages_scanned += 1

            now = time.monotonic()
            if now - last_log_time >= 1.0 or pages_scanned == total_pages:
                remaining = total_pages - pages_scanned
                eta_display = ""
                elapsed = now - overall_start
                rate = pages_scanned / elapsed if elapsed > 0 else 0
                if rate > 0 and remaining > 0:
                    eta_str = _format_eta(remaining / rate)
                    if eta_str:
                        eta_display = f" (ETA: ~{eta_str})"

                log_msg = (
                    f"[{pdf_index}/{total_pdfs}] {pdf_path.name} - "
                    f"page {pages_scanned}/{total_pages}, "
                    f"{len(matches)} match(es) found{eta_display}"
                )
                logger.info(log_msg)
                last_log_time = now

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
        logger.error("[%d/%d] %s - error: %s", pdf_index, total_pdfs, pdf_path.name, exc)
        return matches, audit_rows

    elapsed = time.monotonic() - overall_start
    if matches:
        logger.info(
            "[%d/%d] %s - done, %d match(es) in %s",
            pdf_index, total_pdfs, pdf_path.name, len(matches),
            _format_eta(elapsed) or f"{elapsed:.1f}s",
        )
    else:
        logger.info(
            "[%d/%d] %s - done, no matches (%s)",
            pdf_index, total_pdfs, pdf_path.name,
            _format_eta(elapsed) or f"{elapsed:.1f}s",
        )
        audit_rows.append(
            AuditRow(
                status="no_match",
                source_pdf=pdf_path,
                page_number="",
                matched_numbers="",
                output_pdf="",
                message="No target identifier found",
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