from __future__ import annotations

import pytest

from payslip_extractor.folder_scan import (
    FolderScanCancelled,
    normalize_exclude_terms,
    scan_local_pdf_folder,
    scan_pdf_sources,
)


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
        ("Archive/", "Excluded folder tree by folder/name filter: archive")
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


def test_scan_pdf_sources_includes_only_fuzzy_matching_folder_names(tmp_path) -> None:
    folder_names = [
        "BULLETINS DE PAIE",
        "BULLETIN DE PAIE",
        "BULLETIN   DE PAIE",
        "BULLETINS DE PAIE 082026",
        "BULLETIN DE PAIE 08 2026",
        "BULLETINS DE PAIE 08-2026",
    ]
    for index, folder_name in enumerate(folder_names, start=1):
        folder = tmp_path / folder_name
        folder.mkdir()
        (folder / f"{index}.pdf").write_text("x", encoding="utf-8")

    other = tmp_path / "archives"
    other.mkdir()
    (other / "old.pdf").write_text("x", encoding="utf-8")

    result = scan_pdf_sources(tmp_path, (), "", include_folder_terms="bulletins de paie")

    assert [item.relative_path for item in result.included] == [
        "BULLETIN   DE PAIE/3.pdf",
        "BULLETIN DE PAIE 08 2026/5.pdf",
        "BULLETIN DE PAIE/2.pdf",
        "BULLETINS DE PAIE 08-2026/6.pdf",
        "BULLETINS DE PAIE 082026/4.pdf",
        "BULLETINS DE PAIE/1.pdf",
    ]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("archives/old.pdf", "Folder did not match include filter: bulletins de paie")
    ]


def test_scan_pdf_sources_includes_only_matching_file_names(tmp_path) -> None:
    folder = tmp_path / "legacy"
    folder.mkdir()
    (folder / "BULLETINS-DE-PAIE_08-2026.pdf").write_text("x", encoding="utf-8")
    (folder / "contract.pdf").write_text("x", encoding="utf-8")
    (folder / "payroll-summary.PDF").write_text("x", encoding="utf-8")

    result = scan_pdf_sources(tmp_path, (), "", include_filename_terms="bulletin de paie, payroll")

    assert [item.relative_path for item in result.included] == [
        "legacy/BULLETINS-DE-PAIE_08-2026.pdf",
        "legacy/payroll-summary.PDF",
    ]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("legacy/contract.pdf", "Filename did not match include filter: bulletin de paie, payroll")
    ]


def test_scan_pdf_sources_combines_folder_and_filename_includes(tmp_path) -> None:
    payslips = tmp_path / "BULLETINS DE PAIE"
    contracts = tmp_path / "contracts"
    payslips.mkdir()
    contracts.mkdir()
    (payslips / "august.pdf").write_text("x", encoding="utf-8")
    (payslips / "notes.pdf").write_text("x", encoding="utf-8")
    (contracts / "august.pdf").write_text("x", encoding="utf-8")

    result = scan_pdf_sources(
        tmp_path,
        (),
        "",
        include_folder_terms="bulletin de paie",
        include_filename_terms="august",
    )

    assert [item.relative_path for item in result.included] == ["BULLETINS DE PAIE/august.pdf"]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("BULLETINS DE PAIE/notes.pdf", "Filename did not match include filter: august"),
        ("contracts/august.pdf", "Folder did not match include filter: bulletin de paie"),
    ]


def test_scan_pdf_sources_folder_include_tolerates_typos_and_case(tmp_path) -> None:
    folder = tmp_path / "BULLETINS DE PAIE"
    folder.mkdir()
    (folder / "match.pdf").write_text("x", encoding="utf-8")
    other = tmp_path / "contracts"
    other.mkdir()
    (other / "skip.pdf").write_text("x", encoding="utf-8")

    result = scan_pdf_sources(tmp_path, (), "", include_folder_terms="bulletin de paei")

    assert [item.relative_path for item in result.included] == ["BULLETINS DE PAIE/match.pdf"]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("contracts/skip.pdf", "Folder did not match include filter: bulletin de paei")
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


def test_scan_pdf_sources_prunes_excluded_folder_trees(tmp_path) -> None:
    archive = tmp_path / "archive"
    nested = archive / "nested"
    current = tmp_path / "current"
    nested.mkdir(parents=True)
    current.mkdir()
    (nested / "old.pdf").write_text("x", encoding="utf-8")
    (current / "payroll.pdf").write_text("x", encoding="utf-8")
    messages: list[str] = []

    result = scan_pdf_sources(tmp_path, (), "archive", progress_callback=messages.append)

    assert [item.relative_path for item in result.included] == ["current/payroll.pdf"]
    assert [(item.relative_path, item.reason) for item in result.skipped] == [
        ("archive/", "Excluded folder tree by folder/name filter: archive")
    ]
    assert not any("archive/nested" in message for message in messages)
    assert any("Skipping excluded folder tree: archive/" in message for message in messages)


def test_scan_pdf_sources_can_be_cancelled(tmp_path) -> None:
    folder = tmp_path / "current"
    folder.mkdir()
    (folder / "payroll.pdf").write_text("x", encoding="utf-8")
    calls = 0

    def should_cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(FolderScanCancelled):
        scan_pdf_sources(tmp_path, (), "", cancel_callback=should_cancel)
