from __future__ import annotations

import base64
import io
import json
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from reportlab.pdfgen import canvas

from payslip_extractor.web import WebHandler, bind_web_server, parse_multipart_upload


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


def test_web_extract_endpoint_returns_zip_and_summary(tmp_path) -> None:
    pdf_path = tmp_path / "payroll.pdf"
    _write_pdf(pdf_path, ["Employee 123 page", "Employee 456 page"])
    numbers_path = tmp_path / "numbers.csv"
    numbers_path.write_text("123\n", encoding="utf-8")

    body, content_type = _multipart_body(
        fields={"mode": "separate"},
        files=[
            ("pdf_files", "payroll.pdf", pdf_path.read_bytes()),
            ("numbers_file", "numbers.csv", numbers_path.read_bytes()),
        ],
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        request = urllib.request.Request(
            f"http://{host}:{port}/api/extract",
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read()
            summary = _decode_summary(response.headers["X-Extraction-Summary"])
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

    with zipfile.ZipFile(io.BytesIO(response_body)) as archive:
        names = set(archive.namelist())

    assert {"123.pdf", "audit.csv", "extraction.log"}.issubset(names)


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


def _write_pdf(path: Path, page_texts: list[str]) -> None:
    pdf = canvas.Canvas(str(path))
    for text in page_texts:
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
