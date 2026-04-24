from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from payslip_extractor import __version__
from payslip_extractor.extractor import ExtractionError, run_extraction
from payslip_extractor.gui import UserCancelled, collect_gui_selections
from payslip_extractor.numbers import NumberFileError


class _FlushHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    handler = _FlushHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    logging.basicConfig(level=log_level, handlers=[handler])

    logger = logging.getLogger("payslip_extractor")

    pdf_paths = _flatten_pdf_args(args.pdf)
    numbers_file = Path(args.numbers_file) if args.numbers_file else None
    output_dir = Path(args.output_dir) if args.output_dir else None
    mode = args.mode

    if not args.no_gui and (not pdf_paths or numbers_file is None or output_dir is None):
        logger.info("Awaiting file selection in dialog...")
        try:
            selections = collect_gui_selections(ask_mode=not argv)
        except UserCancelled as exc:
            print(f"Cancelled: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Could not open file selection dialogs: {exc}", file=sys.stderr)
            return 2

        pdf_paths = pdf_paths or selections.pdf_paths
        numbers_file = numbers_file or selections.numbers_file
        output_dir = output_dir or selections.output_dir
        if not argv:
            mode = selections.mode

    missing = []
    if not pdf_paths:
        missing.append("--pdf")
    if numbers_file is None:
        missing.append("--numbers-file")
    if output_dir is None:
        missing.append("--output-dir")
    if missing:
        parser.error(f"missing required arguments: {', '.join(missing)}")

    try:
        summary = run_extraction(
            pdf_paths=pdf_paths,
            numbers_file=numbers_file,
            output_dir=output_dir,
            mode=mode,
        )
    except (ExtractionError, NumberFileError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    print(f"Identifiers loaded: {summary.numbers_count}")
    print(f"Identifiers matched: {summary.matched_numbers_count}")
    print(f"Pages extracted: {summary.matched_pages_count}")
    print(f"Audit report: {summary.audit_csv}")
    if summary.output_files:
        print("Output PDFs:")
        for output_file in summary.output_files:
            print(f"  {output_file}")
    else:
        print("No output PDFs were created because no matching pages were found.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="payslip-extractor",
        description="Extract searchable payslip PDF pages by identifiers from Excel or CSV input.",
    )
    parser.add_argument(
        "--pdf",
        action="append",
        nargs="+",
        metavar="PATH",
        help="PDF file path. Repeat the option or pass multiple paths after one option.",
    )
    parser.add_argument(
        "--numbers-file",
        metavar="PATH",
        help="Excel .xlsx/.xlsm or CSV file containing target identifiers.",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Folder where extracted PDFs and audit.csv will be written.",
    )
    parser.add_argument(
        "--mode",
        choices=["separate", "merged"],
        default="separate",
        help="Output mode: one PDF per identifier or one merged PDF. Default: separate.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Do not open file selection dialogs when arguments are missing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging for more detailed output.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _flatten_pdf_args(pdf_args: list[list[str]] | None) -> list[Path]:
    if not pdf_args:
        return []
    return [Path(path) for group in pdf_args for path in group]