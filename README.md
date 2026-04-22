# Payslip PDF Extractor

Fast local tool for extracting pages from searchable payroll PDFs using employee, CNSS, or other numeric identifiers from an Excel/CSV file.

The Windows build is designed for locked-down computers: the user downloads the executable artifact and runs it without installing Python or packages.

## What It Does

- Reads many large searchable-text PDF files.
- Reads target numbers from `.xlsx`, `.xlsm`, or `.csv`.
- Finds exact digit-token matches only, so `123` does not match `91234`.
- Extracts only pages where a target number appears.
- Writes either one PDF per number or one merged PDF.
- Always writes `audit.csv` with matches, output files, and skipped/error rows.

OCR is not included. Scanned image PDFs must be converted to searchable PDFs before using this tool.

## Windows User Flow

Download one of the GitHub Actions artifacts:

- `payslip-extractor-onefile-windows`: one `.exe`.
- `payslip-extractor-folder-windows`: fallback portable folder if the one-file executable is blocked by antivirus policy.

Double-clicking the executable opens file selection dialogs:

1. Select the PDF files.
2. Select the Excel or CSV file containing numbers.
3. Select the output folder.
4. Choose one merged PDF or one PDF per number.

## CLI Usage

```powershell
payslip-extractor.exe `
  --pdf "C:\paie\janvier.pdf" "C:\paie\fevrier.pdf" `
  --numbers-file "C:\paie\matricules.xlsx" `
  --output-dir "C:\paie\extraits" `
  --mode separate
```

Merged output:

```powershell
payslip-extractor.exe `
  --pdf "C:\paie\bulletins.pdf" `
  --numbers-file "C:\paie\matricules.csv" `
  --output-dir "C:\paie\extraits" `
  --mode merged
```

Options:

- `--mode separate`: creates one PDF per number, for example `12345.pdf`.
- `--mode merged`: creates `matched_pages.pdf`.
- `--no-gui`: fail instead of opening file dialogs when required arguments are missing.
- `--verbose`: print each PDF as it is scanned.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

Run locally:

```bash
payslip-extractor --pdf /path/to/bulletins.pdf --numbers-file /path/to/matricules.xlsx --output-dir outputs --mode separate
```

## Packaging

The Windows executable is built by GitHub Actions on Python 3.12 using PyInstaller. The workflow runs tests first, then uploads both a one-file executable and a zipped portable folder.
