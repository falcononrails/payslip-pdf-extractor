from __future__ import annotations

from payslip_extractor.folder_scan import normalize_exclude_terms, scan_local_pdf_folder, scan_pdf_sources


def test_scan_local_pdf_folder_recurses_sorts_and_skips_by_terms(tmp_path) -> None:
    current = tmp_path / "current"
    archive = tmp_path / "Archive"
    nested = current / "nested"
    for folder in (current, archive, nested):
        folder.mkdir(parents=True)

    (current / "b.pdf").write_text("x", encoding="utf-8")
    (current / "A.PDF").write_text("x", encoding="utf-8")
    (nested / "c.pdf").write_text("x", encoding="utf-8")
    (archive / "old.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "z.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    result = scan_local_pdf_folder(tmp_path, "archive")

    assert [item.relative_path for item in result.included] == [
        "current/A.PDF",
        "current/b.pdf",
        "current/nested/c.pdf",
        "z.pdf",
    ]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("Archive/old.pdf", "Excluded by folder/name filter: archive")
    ]
    assert result.total_pdf_count == 5


def test_normalize_exclude_terms_splits_deduplicates_and_ignores_blank_values() -> None:
    assert normalize_exclude_terms(" archive,backup\nARCHIVE; old ,, ") == ("archive", "backup", "old")


def test_scan_pdf_sources_accepts_individual_pdfs_without_root_folder(tmp_path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "old-second.pdf"
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    result = scan_pdf_sources(None, [str(second), str(first)], "old")

    assert [item.path for item in result.included] == [first]
    assert [(item.path, item.reason) for item in result.skipped] == [
        (second, "Excluded by folder/name filter: old")
    ]


def test_scan_pdf_sources_reports_folder_walk_progress(tmp_path) -> None:
    nested = tmp_path / "network" / "legacy"
    nested.mkdir(parents=True)
    (nested / "payroll.pdf").write_text("x", encoding="utf-8")
    messages: list[str] = []

    scan_pdf_sources(tmp_path, (), "", progress_callback=messages.append)

    assert any(message.startswith("Reading folder 1:") for message in messages)
    assert any("Finished reading" in message for message in messages)
    assert any("Found 1 PDF file(s) so far" in message for message in messages)
