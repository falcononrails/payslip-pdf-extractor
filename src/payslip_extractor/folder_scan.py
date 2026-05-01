from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class FolderPdf:
    path: Path
    relative_path: str
    folder_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkippedFolderPdf:
    path: Path
    relative_path: str
    term: str
    reason_label: str = "Excluded by folder/name filter"

    @property
    def reason(self) -> str:
        return f"{self.reason_label}: {self.term}"


@dataclass(frozen=True)
class FolderScanResult:
    root_path: Path | None
    exclude_terms: tuple[str, ...]
    include_folder_terms: tuple[str, ...]
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


def scan_local_pdf_folder(
    root_path: Path | str,
    exclude_terms: str | list[str] | tuple[str, ...],
    include_folder_terms: str | list[str] | tuple[str, ...] = (),
) -> FolderScanResult:
    return scan_pdf_sources(
        root_path=root_path,
        pdf_paths=(),
        exclude_terms=exclude_terms,
        include_folder_terms=include_folder_terms,
    )


def scan_pdf_sources(
    root_path: Path | str | None,
    pdf_paths: list[str] | tuple[str, ...] | tuple[Path, ...],
    exclude_terms: str | list[str] | tuple[str, ...],
    include_folder_terms: str | list[str] | tuple[str, ...] = (),
    progress_callback: Callable[[str], None] | None = None,
) -> FolderScanResult:
    root = Path(root_path).expanduser().resolve(strict=False) if root_path else None
    if root is not None and not root.exists():
        raise ValueError(f"Folder does not exist: {root}")
    if root is not None and not root.is_dir():
        raise ValueError(f"Path is not a folder: {root}")

    terms = normalize_exclude_terms(exclude_terms)
    folder_terms = normalize_exclude_terms(include_folder_terms)
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
        if folder_terms:
            report(f"Only including PDFs under folder names matching: {', '.join(folder_terms)}")
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
                relative_path = path.relative_to(root)
                seen_paths.add(_path_key(resolved))
                candidates.append(
                    FolderPdf(
                        path=resolved,
                        relative_path=relative_path.as_posix(),
                        folder_names=_folder_names(root.name, relative_path),
                    )
                )
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

        candidates.append(FolderPdf(path=path, relative_path=str(path), folder_names=tuple(path.parent.parts)))
        seen_paths.add(key)
        discovered_count += 1

    report(f"Applying filters to {len(candidates)} PDF file(s).")

    for candidate in sorted(candidates, key=lambda item: item.relative_path.casefold()):
        matched_term = _first_matching_term(candidate.relative_path, terms)
        if matched_term is not None:
            skipped.append(
                SkippedFolderPdf(path=candidate.path, relative_path=candidate.relative_path, term=matched_term)
            )
            continue

        matched_folder_term = _first_matching_folder_term(candidate.folder_names, folder_terms)
        if folder_terms and matched_folder_term is None:
            skipped.append(
                SkippedFolderPdf(
                    path=candidate.path,
                    relative_path=candidate.relative_path,
                    term=", ".join(folder_terms),
                    reason_label="Folder did not match include filter",
                )
            )
            continue

        included.append(candidate)

    return FolderScanResult(
        root_path=root,
        exclude_terms=terms,
        include_folder_terms=folder_terms,
        included=tuple(included),
        skipped=tuple(skipped),
    )


def _first_matching_term(relative_path: str, terms: tuple[str, ...]) -> str | None:
    value = relative_path.casefold()
    for term in terms:
        if term.casefold() in value:
            return term
    return None


def _first_matching_folder_term(folder_names: tuple[str, ...], terms: tuple[str, ...]) -> str | None:
    if not terms:
        return None

    for term in terms:
        if any(_folder_name_matches(folder_name, term) for folder_name in folder_names):
            return term
    return None


def _folder_name_matches(folder_name: str, term: str) -> bool:
    folder_key = _match_key(folder_name)
    term_key = _match_key(term)
    if not folder_key or not term_key:
        return False
    if term_key in folder_key:
        return True

    singular_folder_key = _match_key(folder_name, singularize=True)
    singular_term_key = _match_key(term, singularize=True)
    if singular_term_key in singular_folder_key:
        return True

    if len(singular_term_key) < 4:
        return False

    return _has_near_substring(singular_folder_key, singular_term_key)


def _match_key(value: str, singularize: bool = False) -> str:
    tokens = _normalized_tokens(value)
    if singularize:
        tokens = tuple(_singularize_token(token) for token in tokens)
    return "".join(tokens)


def _normalized_tokens(value: str) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    tokens: list[str] = []
    current: list[str] = []
    for char in folded:
        if unicodedata.combining(char):
            continue
        if char.isalnum():
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _singularize_token(token: str) -> str:
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _has_near_substring(value: str, term: str) -> bool:
    max_distance = _allowed_distance(term)
    min_length = max(1, len(term) - max_distance)
    max_length = min(len(value), len(term) + max_distance)

    if abs(len(value) - len(term)) <= max_distance and _levenshtein_distance(value, term, max_distance) <= max_distance:
        return True

    for length in range(min_length, max_length + 1):
        for start in range(0, len(value) - length + 1):
            if _levenshtein_distance(value[start : start + length], term, max_distance) <= max_distance:
                return True
    return False


def _allowed_distance(term: str) -> int:
    if len(term) <= 5:
        return 1
    if len(term) <= 12:
        return 2
    return 3


def _levenshtein_distance(left: str, right: str, max_distance: int) -> int:
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1
    if left == right:
        return 0

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_minimum = current[0]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    current[right_index - 1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + cost,
                )
            )
            row_minimum = min(row_minimum, current[right_index])
        if row_minimum > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def _folder_names(root_name: str, relative_path: Path) -> tuple[str, ...]:
    parent_parts = tuple(part for part in relative_path.parent.parts if part != ".")
    if root_name:
        return (root_name, *parent_parts)
    return parent_parts


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
