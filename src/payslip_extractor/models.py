from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from payslip_extractor.periods import PayslipPeriod


@dataclass(frozen=True)
class PageMatch:
    source_pdf: Path
    page_index: int
    matched_numbers: tuple[str, ...]
    period: PayslipPeriod | None = None
    scan_order: int = 0

    @property
    def page_number(self) -> int:
        return self.page_index + 1


@dataclass(frozen=True)
class AuditRow:
    status: str
    source_pdf: Path | str
    page_number: int | str
    matched_numbers: tuple[str, ...] | str
    output_pdf: Path | str
    message: str = ""

    def as_csv_row(self) -> dict[str, str]:
        matched_numbers = self.matched_numbers
        if isinstance(matched_numbers, tuple):
            matched_numbers = ";".join(matched_numbers)

        return {
            "status": self.status,
            "source_pdf": str(self.source_pdf),
            "page_number": str(self.page_number),
            "matched_numbers": matched_numbers,
            "output_pdf": str(self.output_pdf),
            "message": self.message,
        }


@dataclass(frozen=True)
class ExtractionSummary:
    numbers_count: int
    matched_numbers_count: int
    matched_pages_count: int
    output_files: tuple[Path, ...]
    audit_csv: Path
