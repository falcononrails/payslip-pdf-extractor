from __future__ import annotations

import base64
import csv
import io
import json
import re
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from reportlab.pdfgen import canvas

from payslip_extractor.web import WebHandler, bind_web_server, build_results_zip_name, parse_multipart_upload


def test_parse_multipart_upload_streams_fields_and_files(tmp_path) -> None:
    body, content_type = _multipart_body(
        fields={"mode": "merged"},
        files=[
            ("pdf_files", "first.pdf", b"%PDF-1.7\nbinary\x00content"),
            ("numbers_file", "numbers.csv", b"123\n"),
        ],
    )

    form = parse_multipart_upload(io.BytesIO(body), content_type, len(body), tmp_path)

    assert form.fields["mode"] == ["merged"]
    assert len(form.files["pdf_files"]) == 1
    assert form.files["pdf_files"][0].path.read_bytes() == b"%PDF-1.7\nbinary\x00content"
    assert form.files["numbers_file"][0].path.read_text(encoding="utf-8") == "123\n"


def test_web_manual_pdf_preview_returns_zip_and_summary(tmp_path) -> None:
    pdf_path = tmp_path / "payroll.pdf"
    _write_pdf(pdf_path, ["Employee 123 page", "Employee 456 page"])
    numbers_path = tmp_path / "numbers.csv"
    numbers_path.write_text("123\n", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        preview = _create_preview(
            base_url,
            {"rootPath": "", "pdfPaths": [str(pdf_path)], "excludeTerms": ""},
        )

        assert preview["totalPdfCount"] == 1
        assert preview["includedCount"] == 1

        body, content_type = _multipart_body(
            fields={"preview_id": str(preview["previewId"]), "mode": "separate"},
            files=[("numbers_file", "numbers.csv", numbers_path.read_bytes())],
        )
        start_payload = _start_folder_job(base_url, body, content_type)

        job = _wait_for_job(base_url, str(start_payload["jobId"]))
        with urllib.request.urlopen(f"{base_url}{job['downloadUrl']}", timeout=30) as response:
            response_body = response.read()
            summary = job["summary"]
            download_summary = _decode_summary(response.headers["X-Extraction-Summary"])
            content_disposition = response.headers["Content-Disposition"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert summary["mode"] == "separate"
    assert summary["pdfCount"] == 1
    assert summary["numbersCount"] == 1
    assert summary["matchedNumbersCount"] == 1
    assert summary["matchedPagesCount"] == 1
    assert summary["outputFilesCount"] == 1
    assert summary["skippedPdfCount"] == 0
    assert download_summary["downloadName"] == summary["downloadName"]
    assert re.fullmatch(r"payslip-extractor-results-\d{8}-\d{6}\.zip", str(summary["downloadName"]))
    assert content_disposition == f'attachment; filename="{summary["downloadName"]}"'
    assert any("Loading identifiers" in message["text"] for message in job["messages"])

    with zipfile.ZipFile(io.BytesIO(response_body)) as archive:
        names = set(archive.namelist())

    assert {"123.pdf", "audit.csv", "extraction.log"}.issubset(names)


def test_web_folder_preview_and_extract_skips_explicitly_filtered_pdfs(tmp_path) -> None:
    current = tmp_path / "current"
    archive = tmp_path / "archive"
    current.mkdir()
    archive.mkdir()
    _write_pdf(current / "payroll.pdf", ["Employee 123 page"])
    _write_pdf(archive / "old.pdf", ["Employee 999 page"])
    numbers_path = tmp_path / "numbers.csv"
    numbers_path.write_text("123\n", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        preview = _create_preview(base_url, {"rootPath": str(tmp_path), "excludeTerms": "archive"})

        assert preview["totalPdfCount"] == 2
        assert preview["includedCount"] == 1
        assert preview["skippedCount"] == 1
        assert preview["includedSamples"] == [{"path": "current/payroll.pdf"}]
        assert preview["skippedSamples"] == [
            {"path": "archive/old.pdf", "reason": "Excluded by folder/name filter: archive"}
        ]

        body, content_type = _multipart_body(
            fields={"preview_id": str(preview["previewId"]), "mode": "separate"},
            files=[("numbers_file", "numbers.csv", numbers_path.read_bytes())],
        )
        start_payload = _start_folder_job(base_url, body, content_type)

        job = _wait_for_job(base_url, str(start_payload["jobId"]))
        with urllib.request.urlopen(f"{base_url}{job['downloadUrl']}", timeout=30) as response:
            response_body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    summary = job["summary"]
    assert summary["pdfCount"] == 1
    assert summary["skippedPdfCount"] == 1
    assert summary["matchedNumbersCount"] == 1

    with zipfile.ZipFile(io.BytesIO(response_body)) as archive_file:
        names = set(archive_file.namelist())
        audit_rows = list(
            csv.DictReader(io.StringIO(archive_file.read("audit.csv").decode("utf-8-sig")))
        )

    assert {"123.pdf", "audit.csv", "extraction.log"}.issubset(names)
    assert any(
        row["status"] == "skipped" and "archive/old.pdf" in row["source_pdf"].replace("\\", "/")
        for row in audit_rows
    )


def test_web_folder_start_skips_manually_removed_preview_pdfs(tmp_path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    _write_pdf(current / "keep.pdf", ["Employee 123 page"])
    _write_pdf(current / "remove.pdf", ["Employee 456 page"])
    numbers_path = tmp_path / "numbers.csv"
    numbers_path.write_text("123\n456\n", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        preview = _create_preview(base_url, {"rootPath": str(tmp_path), "excludeTerms": ""})

        body, content_type = _multipart_body(
            fields={
                "preview_id": str(preview["previewId"]),
                "mode": "separate",
                "removed_paths": json.dumps(["current/remove.pdf"]),
            },
            files=[("numbers_file", "numbers.csv", numbers_path.read_bytes())],
        )
        start_payload = _start_folder_job(base_url, body, content_type)

        job = _wait_for_job(base_url, str(start_payload["jobId"]))
        with urllib.request.urlopen(f"{base_url}{job['downloadUrl']}", timeout=30) as response:
            response_body = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    summary = job["summary"]
    assert summary["pdfCount"] == 1
    assert summary["skippedPdfCount"] == 1
    assert summary["matchedNumbersCount"] == 1

    with zipfile.ZipFile(io.BytesIO(response_body)) as archive_file:
        names = set(archive_file.namelist())
        audit_rows = list(
            csv.DictReader(io.StringIO(archive_file.read("audit.csv").decode("utf-8-sig")))
        )

    assert "123.pdf" in names
    assert "456.pdf" not in names
    assert any(
        row["status"] == "skipped"
        and "current/remove.pdf" in row["source_pdf"].replace("\\", "/")
        and row["message"] == "Removed from preview selection"
        for row in audit_rows
    )


def test_web_folder_preview_uses_include_folder_terms(tmp_path) -> None:
    included_folder = tmp_path / "BULLETINS DE PAIE 08-2026"
    skipped_folder = tmp_path / "contracts"
    included_folder.mkdir()
    skipped_folder.mkdir()
    _write_pdf(included_folder / "payroll.pdf", ["Employee 123 page"])
    _write_pdf(skipped_folder / "contract.pdf", ["Employee 456 page"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        base_url = f"http://{host}:{port}"

        preview = _create_preview(
            base_url,
            {
                "rootPath": str(tmp_path),
                "includeFolderTerms": "bulletin de paei",
                "excludeTerms": "",
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert preview["includeFolderTerms"] == ["bulletin de paei"]
    assert preview["totalPdfCount"] == 2
    assert preview["includedCount"] == 1
    assert preview["skippedCount"] == 1
    assert preview["includedSamples"] == [{"path": "BULLETINS DE PAIE 08-2026/payroll.pdf"}]
    assert preview["skippedSamples"] == [
        {"path": "contracts/contract.pdf", "reason": "Folder did not match include filter: bulletin de paei"}
    ]


def test_bind_web_server_falls_back_when_port_is_busy() -> None:
    first = bind_web_server("127.0.0.1", 0)
    host, port = first.server_address

    try:
        second = bind_web_server(host, port)
        try:
            assert second.server_address[1] != port
        finally:
            second.server_close()
    finally:
        first.server_close()


def test_build_results_zip_name_uses_windows_safe_timestamp() -> None:
    assert build_results_zip_name(datetime(2026, 5, 1, 22, 30, 45)) == (
        "payslip-extractor-results-20260501-223045.zip"
    )


def _multipart_body(
    fields: dict[str, str],
    files: list[tuple[str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = "----PayslipExtractorTestBoundary"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, filename, content in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _decode_summary(value: str) -> dict[str, object]:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _create_preview(base_url: str, payload: dict[str, object]) -> dict[str, object]:
    preview_request = urllib.request.Request(
        f"{base_url}/api/folder/preview",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(preview_request, timeout=30) as response:
        assert response.status == 202
        start_payload = json.loads(response.read().decode("utf-8"))

    preview_job = _wait_for_preview(base_url, str(start_payload["previewJobId"]))
    return preview_job["preview"]  # type: ignore[return-value]


def _start_folder_job(base_url: str, body: bytes, content_type: str) -> dict[str, object]:
    start_request = urllib.request.Request(
        f"{base_url}/api/folder/start",
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(start_request, timeout=30) as response:
        assert response.status == 202
        return json.loads(response.read().decode("utf-8"))


def _wait_for_preview(base_url: str, preview_job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 30
    last_payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"{base_url}/api/previews/{preview_job_id}", timeout=10) as response:
            last_payload = json.loads(response.read().decode("utf-8"))

        if last_payload.get("status") in {"done", "error"}:
            return last_payload

        time.sleep(0.1)

    raise AssertionError(f"Timed out waiting for folder preview: {last_payload}")


def _wait_for_job(base_url: str, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 30
    last_payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"{base_url}/api/jobs/{job_id}", timeout=10) as response:
            last_payload = json.loads(response.read().decode("utf-8"))

        if last_payload.get("status") in {"done", "error"}:
            return last_payload

        time.sleep(0.1)

    raise AssertionError(f"Timed out waiting for extraction job: {last_payload}")


def _write_pdf(path: Path, page_texts: list[str]) -> None:
    pdf = canvas.Canvas(str(path))
    for text in page_texts:
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
