from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

SUPPORTED_NUMBER_FILE_SUFFIXES = {".csv", ".xlsx", ".xlsm"}


class NumberFileError(ValueError):
    """Raised when the number input file cannot be read."""


def read_numbers(path: Path) -> list[str]:
    """Read unique digit-only numbers from a CSV/XLSX/XLSM file."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        values = _iter_csv_values(path)
    elif suffix in {".xlsx", ".xlsm"}:
        values = _iter_workbook_values(path)
    else:
        supported = ", ".join(sorted(SUPPORTED_NUMBER_FILE_SUFFIXES))
        raise NumberFileError(f"Unsupported number file type '{suffix}'. Use {supported}.")

    seen: set[str] = set()
    numbers: list[str] = []
    for value in values:
        candidate = normalize_number_cell(value)
        if candidate and candidate not in seen:
            seen.add(candidate)
            numbers.append(candidate)

    if not numbers:
        raise NumberFileError("No digit-only numbers were found in the number file.")

    return numbers


def normalize_number_cell(value: object) -> str | None:
    """Convert one spreadsheet cell value into a searchable number."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = format(value, "f").rstrip("0").rstrip(".")
        return text if text.isdigit() else None

    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(value.to_integral_value())
        text = format(value.normalize(), "f").rstrip("0").rstrip(".")
        return text if text.isdigit() else None

    text = str(value).strip().removeprefix("'").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text if text.isdigit() else None


def _iter_csv_values(path: Path) -> Iterable[object]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file)
            for row in reader:
                yield from row
    except UnicodeDecodeError:
        with path.open("r", encoding="cp1252", newline="") as csv_file:
            reader = csv.reader(csv_file)
            for row in reader:
                yield from row
    except OSError as exc:
        raise NumberFileError(f"Could not read number file '{path}': {exc}") from exc


def _iter_workbook_values(path: Path) -> Iterable[object]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl exposes several parser-specific exceptions.
        raise NumberFileError(f"Could not read Excel file '{path}': {exc}") from exc

    try:
        for worksheet in workbook.worksheets:
            for row in worksheet.iter_rows(values_only=True):
                yield from row
    finally:
        workbook.close()
