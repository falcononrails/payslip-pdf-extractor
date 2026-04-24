from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf


def iter_page_texts(pdf_path: Path) -> Iterator[tuple[int, str]]:
    """Yield zero-based page indexes and extracted text for a searchable PDF."""
    doc = pymupdf.open(str(pdf_path))
    try:
        for page_index in range(len(doc)):
            text = doc[page_index].get_text() or ""
            yield page_index, text
    finally:
        doc.close()


def get_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF file."""
    doc = pymupdf.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()