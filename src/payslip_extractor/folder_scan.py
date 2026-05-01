from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class FolderPdf:
    path: Path
    relative_path: str


@dataclass(frozen=True)
class SkippedFolderPdf:
    path: Path
    relative_path: str
    term: str

    @property
    def reason(self) -> str:
        return f"Excluded by folder/name filter: {self.term}"


@dataclass(frozen=True)
class FolderScanResult:
    root_path: Path | None
    exclude_terms: tuple[str, ...]
    included: tuple[FolderPdf, ...]
    skipped: tuple[SkippedFolderPdf, ...]

    @property
    def total_pdf_count(self) -> int:
        return len(self.included) + len(self.skipped)


def normalize_exclude_terms(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_terms = value.replace("\n", ",").replace(";", ",").split(",")
    else:
        raw_terms = value

    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        term = str(raw_term).strip()
        if not term:
            continue

        key = term.casefold()
        if key in seen:
            continue

        terms.append(term)
        seen.add(key)

    return tuple(terms)


def scan_local_pdf_folder(root_path: Path | str, exclude_terms: str | list[str] | tuple[str, ...]) -> FolderScanResult:
    return scan_pdf_sources(root_path=root_path, pdf_paths=(), exclude_terms=exclude_terms)


def scan_pdf_sources(
    root_path: Path | str | None,
    pdf_paths: list[str] | tuple[str, ...] | tuple[Path, ...],
    exclude_terms: str | list[str] | tuple[str, ...],
    progress_callback: Callable[[str], None] | None = None,
) -> FolderScanResult:
    root = Path(root_path).expanduser().resolve(strict=False) if root_path else None
    if root is not None and not root.exists():
        raise ValueError(f"Folder does not exist: {root}")
    if root is not None and not root.is_dir():
        raise ValueError(f"Path is not a folder: {root}")

    terms = normalize_exclude_terms(exclude_terms)
    candidates: list[FolderPdf] = []
    included: list[FolderPdf] = []
    skipped: list[SkippedFolderPdf] = []
    seen_paths: set[str] = set()
    discovered_count = 0

    def report(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    if root is not None:
        report(f"Walking folder tree: {root}")
        folder_count = 0

        def on_walk_error(error: OSError) -> None:
            report(f"Could not read folder: {error}")

        for current_root, directories, files in os.walk(root, onerror=on_walk_error):
            directories.sort(key=str.casefold)
            files.sort(key=str.casefold)
            current = Path(current_root)
            folder_count += 1
            if folder_count <= 5 or folder_count % 25 == 0:
                report(f"Reading folder {folder_count}: {_relative_folder_label(current, root)}")

            for filename in files:
                path = current / filename
                if path.suffix.lower() != ".pdf":
                    continue

                resolved = path.resolve(strict=False)
                seen_paths.add(_path_key(resolved))
                candidates.append(FolderPdf(path=resolved, relative_path=path.relative_to(root).as_posix()))
                discovered_count += 1
                if discovered_count == 1 or discovered_count % 100 == 0:
                    report(f"Found {discovered_count} PDF file(s) so far...")

        report(f"Finished reading {folder_count} folder(s). Found {discovered_count} PDF file(s) in the folder tree.")

    if pdf_paths:
        report(f"Checking {len(pdf_paths)} manually selected PDF file(s).")
    for raw_path in pdf_paths:
        path = Path(raw_path).expanduser().resolve(strict=False)
        if path.suffix.lower() != ".pdf":
            continue
        if not path.exists() or not path.is_file():
            continue

        key = _path_key(path)
        if key in seen_paths:
            continue

        candidates.append(FolderPdf(path=path, relative_path=str(path)))
        seen_paths.add(key)
        discovered_count += 1

    report(f"Applying filters to {len(candidates)} PDF file(s).")

    for candidate in sorted(candidates, key=lambda item: item.relative_path.casefold()):
        matched_term = _first_matching_term(candidate.relative_path, terms)
        if matched_term is None:
            included.append(candidate)
        else:
            skipped.append(
                SkippedFolderPdf(path=candidate.path, relative_path=candidate.relative_path, term=matched_term)
            )

    return FolderScanResult(root_path=root, exclude_terms=terms, included=tuple(included), skipped=tuple(skipped))


def _first_matching_term(relative_path: str, terms: tuple[str, ...]) -> str | None:
    value = relative_path.casefold()
    for term in terms:
        if term.casefold() in value:
            return term
    return None


def _relative_folder_label(path: Path, root: Path) -> str:
    if path == root:
        return "."
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _path_key(path: Path) -> str:
    value = str(path)
    return value.casefold() if os.name == "nt" else value
