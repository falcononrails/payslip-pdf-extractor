from __future__ import annotations

import csv
import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

from payslip_extractor.matching import find_matching_numbers
from payslip_extractor.models import AuditRow, ExtractionSummary, PageMatch
from payslip_extractor.numbers import read_numbers
from payslip_extractor.periods import detect_payslip_period
from payslip_extractor.pdf_text import get_page_count, iter_page_texts

OutputMode = Literal["separate", "merged"]

logger = logging.getLogger("payslip_extractor")
PROGRESS_LOG_INTERVAL_PAGES = 50
DEFAULT_PARALLEL_PDF_WORKERS = 8

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


@dataclass(frozen=True)
class PdfScanResult:
    pdf_index: int
    pdf_path: Path
    total_pages: int
    matches: tuple[PageMatch, ...]
    audit_rows: tuple[AuditRow, ...]
    elapsed_seconds: float
    error: str = ""


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


def run_extraction(
    pdf_paths: Iterable[Path | str],
    numbers_file: Path | str,
    output_dir: Path | str,
    mode: OutputMode,
    extra_audit_rows: Iterable[AuditRow] = (),
    worker_count: int | None = None,
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

    matches, audit_rows = scan_pdfs(pdfs, numbers, worker_count=worker_count)

    matched_numbers_set = {number for match in matches for number in match.matched_numbers}
    logger.info(
        "Scan complete. %d/%d identifier(s) matched across %d page(s).",
        len(matched_numbers_set),
        len(numbers),
        len(matches),
    )
    dated_matches = sum(1 for match in matches if match.period is not None)
    if matches:
        logger.info("Detected payslip period for %d/%d matched page(s).", dated_matches, len(matches))

    output_files: list[Path] = []
    if matches:
        logger.info("Writing output PDFs to %s...", output_path)
        with PdfDocumentCache() as reader_cache:
            if mode == "merged":
                merged_pdf = output_path / "matched_pages.pdf"
                sorted_matches = sort_matches_for_output(matches)
                _write_matches_pdf(merged_pdf, sorted_matches, reader_cache)
                output_files.append(merged_pdf)
                audit_rows.extend(_matched_audit_rows(sorted_matches, merged_pdf))
            else:
                output_files.extend(
                    _write_separate_outputs(output_path, numbers, matches, reader_cache, audit_rows)
                )
        logger.info("Done. Wrote %d output file(s).", len(output_files))
    else:
        logger.info("No matching pages found. No output PDFs created.")

    audit_csv = output_path / "audit.csv"
    audit_rows.extend(extra_audit_rows)
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
    worker_count: int | None = None,
) -> tuple[list[PageMatch], list[AuditRow]]:
    pdf_list = list(pdf_paths)
    total_pdfs = len(pdf_list)
    resolved_worker_count = _parallel_worker_count(total_pdfs, worker_count)
    if resolved_worker_count > 1:
        return _scan_pdfs_parallel(pdf_list, numbers, resolved_worker_count)

    all_matches: list[PageMatch] = []
    all_audit_rows: list[AuditRow] = []
    number_set = set(numbers)

    pdf_page_counts = {p: _get_total_pages(p) for p in pdf_list}
    total_pages_all = sum(pdf_page_counts.values())

    logger.info("Scanning %d PDF file(s), %d total pages...", total_pdfs, total_pages_all)

    overall_start = time.monotonic()
    total_pages_scanned = 0

    for pdf_index, pdf_path in enumerate(pdf_list, 1):
        total_pages = pdf_page_counts[pdf_path]
        pages_scanned = 0

        msg = f"[{pdf_index}/{total_pdfs}] {pdf_path.name} ({total_pages} pages) - scanning..."
        logger.info(msg)

        pdf_start = time.monotonic()
        found_in_pdf = False
        pdf_matches = 0

        try:
            for page_index, text in iter_page_texts(pdf_path):
                page_matches = find_matching_numbers(text, numbers, number_set)
                if page_matches:
                    found_in_pdf = True
                    pdf_matches += 1
                    period = detect_payslip_period(text, pdf_path.name, pdf_path.parent)
                    all_matches.append(
                        PageMatch(
                            pdf_path,
                            page_index,
                            page_matches,
                            period=period,
                            scan_order=((pdf_index - 1) * 1_000_000) + page_index,
                        )
                    )

                pages_scanned += 1
                total_pages_scanned += 1

                if _should_log_page_progress(pages_scanned, total_pages, bool(page_matches)):
                    remaining_pages = total_pages_all - total_pages_scanned
                    eta_display = ""
                    if total_pages_scanned > 1:
                        overall_elapsed = time.monotonic() - overall_start
                        rate = total_pages_scanned / overall_elapsed if overall_elapsed > 0 else 0
                        if rate > 0 and remaining_pages > 0:
                            eta_secs = remaining_pages / rate
                            eta_str = _format_eta(eta_secs)
                            if eta_str:
                                eta_display = f" (ETA: ~{eta_str})"

                    log_msg = (
                        f"[{pdf_index}/{total_pdfs}] {pdf_path.name} - "
                        f"page {pages_scanned}/{total_pages}, "
                        f"{len(all_matches)} matched page(s){eta_display}"
                    )
                    logger.info(log_msg)

        except Exception as exc:
            all_audit_rows.append(
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
            continue

        pdf_elapsed = time.monotonic() - pdf_start
        if found_in_pdf:
            logger.info(
                "[%d/%d] %s - done, %d match(es) in %s",
                pdf_index,
                total_pdfs,
                pdf_path.name,
                pdf_matches,
                _format_eta(pdf_elapsed) or f"{pdf_elapsed:.1f}s",
            )
        else:
            logger.info(
                "[%d/%d] %s - done, no matches (%s)",
                pdf_index,
                total_pdfs,
                pdf_path.name,
                _format_eta(pdf_elapsed) or f"{pdf_elapsed:.1f}s",
            )
            all_audit_rows.append(
                AuditRow(
                    status="no_match",
                    source_pdf=pdf_path,
                    page_number="",
                    matched_numbers="",
                    output_pdf="",
                    message="No target identifier found",
                )
            )

    overall_elapsed = time.monotonic() - overall_start
    logger.info(
        "Finished scanning %d pages in %s.",
        total_pages_scanned,
        _format_eta(overall_elapsed) or f"{overall_elapsed:.1f}s",
    )

    return all_matches, all_audit_rows


def _scan_pdfs_parallel(
    pdf_list: list[Path],
    numbers: list[str],
    worker_count: int,
) -> tuple[list[PageMatch], list[AuditRow]]:
    total_pdfs = len(pdf_list)
    number_set = set(numbers)
    logger.info("Scanning %d PDF file(s) with up to %d worker(s)...", total_pdfs, worker_count)

    overall_start = time.monotonic()
    results: list[PdfScanResult] = []
    completed_pages = 0
    completed_matches = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_scan_one_pdf, pdf_index, pdf_path, numbers, number_set)
            for pdf_index, pdf_path in enumerate(pdf_list, start=1)
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed_pages += result.total_pages
            completed_matches += len(result.matches)
            if result.error:
                logger.error("[%d/%d] %s - error: %s", result.pdf_index, total_pdfs, result.pdf_path.name, result.error)
            elif result.matches:
                logger.info(
                    "[%d/%d] %s - done, %d matched page(s) in %s",
                    result.pdf_index,
                    total_pdfs,
                    result.pdf_path.name,
                    len(result.matches),
                    _format_eta(result.elapsed_seconds) or f"{result.elapsed_seconds:.1f}s",
                )
            else:
                logger.info(
                    "[%d/%d] %s - done, no matches (%s)",
                    result.pdf_index,
                    total_pdfs,
                    result.pdf_path.name,
                    _format_eta(result.elapsed_seconds) or f"{result.elapsed_seconds:.1f}s",
                )

            remaining = total_pdfs - len(results)
            eta_display = ""
            if len(results) > 0 and remaining > 0:
                elapsed = time.monotonic() - overall_start
                rate = len(results) / elapsed if elapsed > 0 else 0
                if rate > 0:
                    eta = _format_eta(remaining / rate)
                    if eta:
                        eta_display = f" (ETA: ~{eta})"
            logger.info(
                "Parallel scan progress: %d/%d PDF(s), %d page(s), %d matched page(s)%s",
                len(results),
                total_pdfs,
                completed_pages,
                completed_matches,
                eta_display,
            )

    ordered_results = sorted(results, key=lambda item: item.pdf_index)
    all_matches = [match for result in ordered_results for match in result.matches]
    all_audit_rows = [row for result in ordered_results for row in result.audit_rows]

    overall_elapsed = time.monotonic() - overall_start
    logger.info(
        "Finished scanning %d pages in %s.",
        completed_pages,
        _format_eta(overall_elapsed) or f"{overall_elapsed:.1f}s",
    )

    return all_matches, all_audit_rows


def _scan_one_pdf(
    pdf_index: int,
    pdf_path: Path,
    numbers: list[str],
    number_set: set[str],
) -> PdfScanResult:
    start = time.monotonic()
    matches: list[PageMatch] = []
    audit_rows: list[AuditRow] = []
    total_pages = 0
    try:
        doc = pymupdf.open(str(pdf_path))
        try:
            total_pages = len(doc)
            for page_index in range(total_pages):
                text = doc[page_index].get_text() or ""
                page_matches = find_matching_numbers(text, numbers, number_set)
                if page_matches:
                    matches.append(
                        PageMatch(
                            pdf_path,
                            page_index,
                            page_matches,
                            period=detect_payslip_period(text, pdf_path.name, pdf_path.parent),
                            scan_order=((pdf_index - 1) * 1_000_000) + page_index,
                        )
                    )
        finally:
            doc.close()
    except Exception as exc:
        return PdfScanResult(
            pdf_index=pdf_index,
            pdf_path=pdf_path,
            total_pages=total_pages,
            matches=(),
            audit_rows=(
                AuditRow(
                    status="error",
                    source_pdf=pdf_path,
                    page_number="",
                    matched_numbers="",
                    output_pdf="",
                    message=str(exc),
                ),
            ),
            elapsed_seconds=time.monotonic() - start,
            error=str(exc),
        )

    if not matches:
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

    return PdfScanResult(
        pdf_index=pdf_index,
        pdf_path=pdf_path,
        total_pages=total_pages,
        matches=tuple(matches),
        audit_rows=tuple(audit_rows),
        elapsed_seconds=time.monotonic() - start,
    )


def _parallel_worker_count(total_pdfs: int, requested_worker_count: int | None = None) -> int:
    if total_pdfs <= 1:
        return 1

    if requested_worker_count is not None:
        configured = requested_worker_count
    else:
        raw_value = os.environ.get("PAYSLIP_EXTRACTOR_WORKERS", "").strip()
        if raw_value:
            try:
                configured = int(raw_value)
            except ValueError:
                configured = DEFAULT_PARALLEL_PDF_WORKERS
        else:
            configured = DEFAULT_PARALLEL_PDF_WORKERS

    if configured <= 1:
        return 1

    cpu_count = os.cpu_count() or 2
    return max(1, min(total_pdfs, configured, cpu_count))


def _should_log_page_progress(pages_scanned: int, total_pages: int, page_has_match: bool) -> bool:
    return (
        page_has_match
        or pages_scanned == 1
        or pages_scanned == total_pages
        or pages_scanned % PROGRESS_LOG_INTERVAL_PAGES == 0
    )


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
    reader_cache: "PdfDocumentCache",
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
        sorted_matches = sort_matches_for_output(number_matches)
        _write_matches_pdf(output_pdf, sorted_matches, reader_cache)
        output_files.append(output_pdf)
        audit_rows.extend(_matched_audit_rows(sorted_matches, output_pdf, only_number=number))

    return output_files


def _write_matches_pdf(
    output_pdf: Path,
    matches: list[PageMatch],
    reader_cache: "PdfDocumentCache",
) -> None:
    output_doc = pymupdf.open()
    try:
        for match in sort_matches_for_output(matches):
            source_doc = reader_cache.get(match.source_pdf)
            output_doc.insert_pdf(source_doc, from_page=match.page_index, to_page=match.page_index)
        output_doc.save(str(output_pdf))
    finally:
        output_doc.close()


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


def sort_matches_for_output(matches: Iterable[PageMatch]) -> list[PageMatch]:
    return sorted(matches, key=_match_output_sort_key)


def _match_output_sort_key(match: PageMatch) -> tuple[int, int, int, int]:
    if match.period is None:
        return (1, 9999, 99, match.scan_order)
    return (0, match.period.year, match.period.month, match.scan_order)


def _safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return name or "number"


class PdfDocumentCache:
    def __init__(self) -> None:
        self._documents: dict[Path, pymupdf.Document] = {}

    def __enter__(self) -> "PdfDocumentCache":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def get(self, path: Path) -> pymupdf.Document:
        if path not in self._documents:
            self._documents[path] = pymupdf.open(str(path))
        return self._documents[path]

    def close(self) -> None:
        for document in self._documents.values():
            document.close()
        self._documents.clear()
