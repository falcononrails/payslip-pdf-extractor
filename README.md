# Payslip PDF Extractor

Fast local tool for extracting pages from searchable payroll PDFs using employee, CNSS, or other numeric identifiers from pasted text, Excel, CSV, or plain text files.

The Windows build is designed for locked-down computers: the user downloads the executable artifact and runs it without installing Python or packages.

## What It Does

- Reads many large searchable-text PDF files.
- Reads target numbers from pasted text, `.xlsx`, `.xlsm`, `.csv`, or plain text files.
- Finds exact identifier-token matches only, so `123` does not match `91234`.
- Extracts only pages where a target number appears, sorted by detected payslip year/month when possible.
- Writes either one PDF per number or one merged PDF.
- Always writes `audit.csv` with matches, output files, and skipped/error rows.

OCR is not included. Scanned image PDFs must be converted to searchable PDFs before using this tool.

## Windows User Flow

Download one of the GitHub Actions artifacts:

- `payslip-extractor-onefile-windows`: one `.exe`.
- `payslip-extractor-folder-windows`: fallback portable folder if the one-file executable is blocked by antivirus policy.

Double-clicking the executable opens a local browser UI. PDF folders/files are selected by the local Python app and read from their existing paths; PDFs are not browser-uploaded or copied before preview. Pasted identifiers or the selected identifier file are copied into the temporary local job workspace when extraction starts.

1. Choose a root folder, add individual PDF files, or do both. The local app reads those PDF paths directly.
2. Enter optional include folder names such as `bulletin de paie` to keep only matching folders.
3. Enter optional include PDF filename terms such as `paie`, `bulletin`, or `payroll` to keep only matching PDF names.
4. Enter folder/name exclude terms such as `archive`, `backup`, `old`, or `temp` when needed.
5. Preview the PDF selection and confirm the selected/skipped counts.
6. Paste identifiers, choose an identifier file, or do both.
7. Choose one merged PDF or one PDF per number.
8. Process the preview, follow progress in the browser, and download the ZIP containing extracted PDFs, `audit.csv`, and `extraction.log`.

The browser UI keeps local run history with summary stats in browser localStorage. The history is stored only on that computer.
The Progress view shows extraction progress. On shared folders or VPN paths, preview shows an inline status while the Python app lists directory entries and reports folder-walk/PDF counts. Excluded folder trees are pruned before entering them, which makes large archive/backup folders faster to skip. Repeating the same preview within a short window can reuse the in-memory scan cache. During extraction, the Progress view shows PDF/page scanning progress, packaging, and download readiness. Skipped PDFs or skipped folder trees are written to `audit.csv` with status `skipped`.
Include folder matching ignores case, accents, repeated spaces, punctuation, singular/plural `s`, and date suffixes, so `bulletin de paie` matches names like `BULLETINS DE PAIE`, `BULLETIN   DE PAIE`, and `BULLETINS DE PAIE 08-2026`.
Include filename matching uses the same tolerant matching against the PDF file name. Filter fields accept comma-separated, semicolon-separated, or newline-separated terms.
Matched pages are sorted oldest-to-newest by detected payslip period before output PDFs are written. The detector supports numeric periods such as `08/2026`, `08-2026`, `08 2026`, `082026`, and month names such as `août 2026` / `AOUT 2026`. Pages without a detected period stay after dated pages in scan order.
Extraction scans multiple PDFs in parallel by default, capped conservatively. Set `PAYSLIP_EXTRACTOR_WORKERS=1` to disable parallel PDF scanning, or a small value such as `2` or `4` to tune it for local disks versus VPN/shared folders.

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
  --numbers-file "C:\paie\matricules.txt" `
  --output-dir "C:\paie\extraits" `
  --mode merged
```

Options:

- `--mode separate`: creates one PDF per number, for example `12345.pdf`.
- `--mode merged`: creates `matched_pages.pdf`.
- `--web`: starts the local browser UI.
- `--gui`: uses the older native file selection dialogs.
- `--port`: changes the local browser UI port. The default `8765` keeps browser history stable.
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
