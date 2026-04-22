from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium


def iter_page_texts(pdf_path: Path) -> Iterator[tuple[int, str]]:
    """Yield zero-based page indexes and extracted text for a searchable PDF."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                textpage = page.get_textpage()
                try:
                    yield page_index, textpage.get_text_range() or ""
                finally:
                    _close_if_possible(textpage)
            finally:
                _close_if_possible(page)
    finally:
        _close_if_possible(document)


def _close_if_possible(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()
