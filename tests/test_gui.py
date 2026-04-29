from __future__ import annotations

from pathlib import Path

from payslip_extractor.gui import add_unique_pdf_paths, pdf_files_in_folder


def test_add_unique_pdf_paths_adds_pdfs_and_skips_duplicates(tmp_path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.PDF"
    ignored = tmp_path / "notes.txt"
    for path in (first, second, ignored):
        path.write_text("x", encoding="utf-8")

    existing: list[Path] = []

    added = add_unique_pdf_paths(existing, [first, second, ignored, first])

    assert added == 2
    assert existing == [first.resolve(), second.resolve()]


def test_pdf_files_in_folder_returns_sorted_non_recursive_pdfs(tmp_path) -> None:
    (tmp_path / "b.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "A.PDF").write_text("x", encoding="utf-8")
    (tmp_path / "z.txt").write_text("x", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "nested.pdf").write_text("x", encoding="utf-8")

    assert [path.name for path in pdf_files_in_folder(tmp_path)] == ["A.PDF", "b.pdf"]
