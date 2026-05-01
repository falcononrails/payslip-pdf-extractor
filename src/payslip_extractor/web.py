from __future__ import annotations

import base64
import io
import json
import logging
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from payslip_extractor import __version__
from payslip_extractor.extractor import ExtractionError, run_extraction
from payslip_extractor.numbers import NumberFileError

logger = logging.getLogger("payslip_extractor")
READ_CHUNK_SIZE = 1024 * 1024
DEFAULT_WEB_PORT = 8765
MAX_JOB_MESSAGES = 300
JOB_RETENTION_SECONDS = 60 * 60
JOBS: dict[str, "ExtractionJob"] = {}
JOBS_LOCK = threading.Lock()


@dataclass(frozen=True)
class UploadedFile:
    field_name: str
    filename: str
    path: Path


@dataclass(frozen=True)
class MultipartForm:
    fields: dict[str, list[str]]
    files: dict[str, list[UploadedFile]]


@dataclass
class ExtractionJob:
    id: str
    workspace: Path
    pdf_paths: tuple[Path, ...]
    numbers_file: Path
    mode: str
    status: str = "queued"
    messages: list[dict[str, str]] = field(default_factory=list)
    summary_payload: dict[str, object] | None = None
    zip_path: Path | None = None
    error: str | None = None
    completed_monotonic: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append_message(self, text: str, level: str = "info") -> None:
        with self._lock:
            self.messages.append(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level,
                    "text": text,
                }
            )
            if len(self.messages) > MAX_JOB_MESSAGES:
                del self.messages[: len(self.messages) - MAX_JOB_MESSAGES]

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    def mark_done(self, summary_payload: dict[str, object], zip_path: Path) -> None:
        with self._lock:
            self.status = "done"
            self.summary_payload = summary_payload
            self.zip_path = zip_path
            self.completed_monotonic = time.monotonic()

    def mark_error(self, error: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = error
            self.completed_monotonic = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            payload: dict[str, object] = {
                "jobId": self.id,
                "status": self.status,
                "messages": list(self.messages),
            }
            if self.error:
                payload["error"] = self.error
            if self.summary_payload:
                payload["summary"] = dict(self.summary_payload)
                payload["downloadName"] = self.summary_payload.get("downloadName", "")
                payload["downloadUrl"] = f"/api/jobs/{self.id}/download"
            return payload

    def is_expired(self, now: float) -> bool:
        with self._lock:
            return self.completed_monotonic is not None and now - self.completed_monotonic > JOB_RETENTION_SECONDS


class LocalWebServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def launch_web_app(host: str = "127.0.0.1", port: int = DEFAULT_WEB_PORT, open_browser: bool = True) -> int:
    server = bind_web_server(host, port)
    actual_host, actual_port = server.server_address
    url = f"http://{actual_host}:{actual_port}/"

    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()

    print(f"Payslip PDF Extractor web UI: {url}", flush=True)
    print("Keep this window open while using the web UI. Press Ctrl+C to stop.", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI.", flush=True)
    finally:
        server.server_close()

    return 0


def bind_web_server(host: str, port: int) -> ThreadingHTTPServer:
    candidate_ports = [0] if port == 0 else list(range(port, min(port + 20, 65536)))
    last_error: OSError | None = None

    for candidate_port in candidate_ports:
        try:
            return LocalWebServer((host, candidate_port), WebHandler)
        except OSError as exc:
            last_error = exc

    raise RuntimeError(f"Could not start local web UI: {last_error}") from last_error


class WebHandler(BaseHTTPRequestHandler):
    server_version = f"PayslipExtractor/{__version__}"

    def log_message(self, format: str, *args: object) -> None:
        logger.info("web: " + format, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_asset("index.html", "text/html; charset=utf-8")
            return
        if path == "/health":
            self._send_json({"ok": True, "version": __version__})
            return
        if path.startswith("/api/jobs/"):
            self._handle_job_request(path)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/extract":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            self._start_extract_job()
        except UserFacingWebError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
        except Exception as exc:
            logger.exception("Unexpected web extraction failure")
            self._send_json({"error": f"Unexpected error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_asset(self, name: str, content_type: str) -> None:
        asset = resources.files("payslip_extractor.web_assets").joinpath(name)
        data = asset.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _start_extract_job(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            raise UserFacingWebError("No upload body received.")

        workspace = Path(tempfile.mkdtemp(prefix="payslip-web-"))
        try:
            form = parse_multipart_upload(self.rfile, self.headers.get("Content-Type", ""), content_length, workspace)

            mode = first_form_value(form.fields, "mode", "separate")
            if mode not in {"separate", "merged"}:
                raise UserFacingWebError("Invalid output mode.")

            pdf_files = form.files.get("pdf_files", [])
            number_files = form.files.get("numbers_file", [])
            if not pdf_files:
                raise UserFacingWebError("Select at least one PDF file.")
            if not number_files:
                raise UserFacingWebError("Select an Excel or CSV identifier file.")

            job = ExtractionJob(
                id=uuid.uuid4().hex,
                workspace=workspace,
                pdf_paths=tuple(upload.path for upload in pdf_files),
                numbers_file=number_files[0].path,
                mode=mode,
            )
            job.append_message("Upload received. Preparing extraction.")
            register_job(job)
            thread = threading.Thread(target=run_extraction_job, args=(job,), daemon=True)
            thread.start()

            self._send_json(
                {
                    "jobId": job.id,
                    "status": job.status,
                    "statusUrl": f"/api/jobs/{job.id}",
                },
                status=HTTPStatus.ACCEPTED,
            )
            workspace = None
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)

    def _handle_job_request(self, path: str) -> None:
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(/download)?", path)
        if not match:
            self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
            return

        job = get_job(match.group(1))
        if job is None:
            self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
            return

        if not match.group(2):
            self._send_json(job.snapshot())
            return

        with job._lock:
            status = job.status
            zip_path = job.zip_path
            summary_payload = dict(job.summary_payload or {})

        if status != "done" or zip_path is None:
            self._send_json({"error": "Extraction is not ready for download."}, status=HTTPStatus.CONFLICT)
            return

        download_name = str(summary_payload.get("downloadName") or zip_path.name)
        self._send_file(zip_path, "application/zip", download_name, summary_payload)

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(
        self,
        path: Path,
        content_type: str,
        download_name: str,
        summary_payload: dict[str, object],
    ) -> None:
        encoded_summary = base64.urlsafe_b64encode(json.dumps(summary_payload).encode("utf-8")).decode("ascii")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("X-Extraction-Summary", encoded_summary)
        self.end_headers()

        with path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile, length=READ_CHUNK_SIZE)


class UserFacingWebError(RuntimeError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


class ThreadLogFilter(logging.Filter):
    def __init__(self, thread_id: int) -> None:
        super().__init__()
        self.thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self.thread_id


class JobLogHandler(logging.Handler):
    def __init__(self, job: ExtractionJob) -> None:
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.job.append_message(self.format(record), record.levelname.lower())
        except Exception:
            self.handleError(record)


def register_job(job: ExtractionJob) -> None:
    cleanup_finished_jobs()
    with JOBS_LOCK:
        JOBS[job.id] = job


def get_job(job_id: str) -> ExtractionJob | None:
    cleanup_finished_jobs()
    with JOBS_LOCK:
        return JOBS.get(job_id)


def cleanup_finished_jobs() -> None:
    now = time.monotonic()
    expired_jobs: list[ExtractionJob] = []
    with JOBS_LOCK:
        for job_id, job in list(JOBS.items()):
            if job.is_expired(now):
                expired_jobs.append(job)
                del JOBS[job_id]

    for job in expired_jobs:
        shutil.rmtree(job.workspace, ignore_errors=True)


def run_extraction_job(job: ExtractionJob) -> None:
    job.set_status("running")
    output_dir = job.workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "extraction.log"

    thread_filter = ThreadLogFilter(threading.get_ident())
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="w")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(thread_filter)
    job_handler = JobLogHandler(job)
    job_handler.setFormatter(formatter)
    job_handler.addFilter(thread_filter)

    logger.addHandler(file_handler)
    logger.addHandler(job_handler)
    logger.setLevel(logging.INFO)

    start = time.monotonic()
    try:
        logger.info("Starting extraction job.")
        summary = run_extraction(
            pdf_paths=job.pdf_paths,
            numbers_file=job.numbers_file,
            output_dir=output_dir,
            mode=job.mode,  # type: ignore[arg-type]
        )

        duration_seconds = round(time.monotonic() - start, 2)
        created_at = datetime.now().astimezone()
        zip_path = job.workspace / build_results_zip_name(created_at)
        logger.info("Packaging results into %s...", zip_path.name)
        create_results_zip(zip_path, output_dir)
        summary_payload = {
            "mode": job.mode,
            "pdfCount": len(job.pdf_paths),
            "numbersCount": summary.numbers_count,
            "matchedNumbersCount": summary.matched_numbers_count,
            "matchedPagesCount": summary.matched_pages_count,
            "outputFilesCount": len(summary.output_files),
            "outputFileNames": [path.name for path in summary.output_files],
            "durationSeconds": duration_seconds,
            "downloadName": zip_path.name,
            "createdAt": created_at.isoformat(timespec="seconds"),
        }
        logger.info("Download ready.")
        job.mark_done(summary_payload, zip_path)
    except (ExtractionError, NumberFileError) as exc:
        job.append_message(str(exc), "error")
        job.mark_error(str(exc))
    except Exception as exc:
        logger.exception("Unexpected web extraction failure")
        job.mark_error(f"Unexpected error: {exc}")
    finally:
        logger.removeHandler(file_handler)
        logger.removeHandler(job_handler)
        file_handler.close()
        job_handler.close()


def parse_multipart_upload(
    stream: BinaryIO,
    content_type: str,
    content_length: int,
    workspace: Path,
) -> MultipartForm:
    boundary = get_multipart_boundary(content_type)
    parser = MultipartStreamParser(stream, content_length, boundary, workspace)
    return parser.parse()


def get_multipart_boundary(content_type: str) -> bytes:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise UserFacingWebError("Expected multipart form upload.")
    boundary = (match.group(1) or match.group(2)).strip()
    if not boundary:
        raise UserFacingWebError("Multipart boundary is missing.")
    return boundary.encode("utf-8")


class MultipartStreamParser:
    def __init__(self, stream: BinaryIO, content_length: int, boundary: bytes, workspace: Path) -> None:
        self.stream = stream
        self.remaining = content_length
        self.boundary = boundary
        self.buffer = bytearray()
        self.upload_dir = workspace / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.file_counter = 0

    def parse(self) -> MultipartForm:
        fields: dict[str, list[str]] = defaultdict(list)
        files: dict[str, list[UploadedFile]] = defaultdict(list)

        if not self._consume_initial_boundary():
            raise UserFacingWebError("Invalid multipart upload.")

        while True:
            headers = self._read_until(b"\r\n\r\n")
            if headers is None:
                break

            header_map = parse_part_headers(headers)
            disposition = header_map.get("content-disposition", "")
            field_name, filename = parse_content_disposition(disposition)
            if not field_name:
                self._discard_part_content()
            elif filename:
                self.file_counter += 1
                safe_name = safe_upload_filename(filename)
                upload_path = self.upload_dir / f"{self.file_counter:04d}-{safe_name}"
                self._write_part_content(upload_path)
                files[field_name].append(UploadedFile(field_name, filename, upload_path))
            else:
                content = io.BytesIO()
                self._write_part_content_to(content)
                fields[field_name].append(content.getvalue().decode("utf-8", errors="replace"))

            boundary_status = self._consume_boundary_suffix()
            if boundary_status == "final":
                break

        return MultipartForm(fields=dict(fields), files=dict(files))

    def _consume_initial_boundary(self) -> bool:
        initial = b"--" + self.boundary
        preamble = self._read_until(initial)
        if preamble not in (b"", None):
            preamble = preamble.strip()
        if preamble not in (b"", None):
            return False
        return self._consume_boundary_suffix() != "invalid"

    def _write_part_content(self, path: Path) -> None:
        with path.open("wb") as output:
            self._write_part_content_to(output)

    def _discard_part_content(self) -> None:
        self._write_part_content_to(io.BytesIO())

    def _write_part_content_to(self, output: BinaryIO) -> None:
        marker = b"\r\n--" + self.boundary
        keep = len(marker) + 4

        while True:
            idx = self.buffer.find(marker)
            if idx >= 0:
                output.write(self.buffer[:idx])
                del self.buffer[: idx + len(marker)]
                return

            if self.remaining <= 0:
                if self.buffer:
                    output.write(self.buffer)
                    self.buffer.clear()
                return

            if len(self.buffer) > keep:
                flush_len = len(self.buffer) - keep
                output.write(self.buffer[:flush_len])
                del self.buffer[:flush_len]

            self._read_more()

    def _consume_boundary_suffix(self) -> str:
        self._ensure_buffer(2)
        if self.buffer.startswith(b"--"):
            del self.buffer[:2]
            self._consume_optional_linebreak()
            return "final"
        if self.buffer.startswith(b"\r\n"):
            del self.buffer[:2]
            return "next"
        if self.buffer.startswith(b"\n"):
            del self.buffer[:1]
            return "next"
        return "invalid"

    def _consume_optional_linebreak(self) -> None:
        self._ensure_buffer(2)
        if self.buffer.startswith(b"\r\n"):
            del self.buffer[:2]
        elif self.buffer.startswith(b"\n"):
            del self.buffer[:1]

    def _read_until(self, delimiter: bytes) -> bytes | None:
        while True:
            idx = self.buffer.find(delimiter)
            if idx >= 0:
                data = bytes(self.buffer[:idx])
                del self.buffer[: idx + len(delimiter)]
                return data

            if self.remaining <= 0:
                return None
            self._read_more()

    def _ensure_buffer(self, size: int) -> None:
        while len(self.buffer) < size and self.remaining > 0:
            self._read_more()

    def _read_more(self) -> None:
        if self.remaining <= 0:
            return
        chunk = self.stream.read(min(READ_CHUNK_SIZE, self.remaining))
        if not chunk:
            self.remaining = 0
            return
        self.buffer.extend(chunk)
        self.remaining -= len(chunk)


def parse_part_headers(raw_headers: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw_headers.decode("utf-8", errors="replace").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def parse_content_disposition(disposition: str) -> tuple[str | None, str | None]:
    parts = [part.strip() for part in disposition.split(";")]
    values: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip().lower()] = value.strip().strip('"')
    return values.get("name"), values.get("filename")


def safe_upload_filename(filename: str) -> str:
    name = Path(filename).name
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return safe or "upload.bin"


def first_form_value(fields: dict[str, list[str]], name: str, default: str) -> str:
    values = fields.get(name)
    if not values:
        return default
    return values[0]


def build_results_zip_name(created_at: datetime | None = None) -> str:
    timestamp = (created_at or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    return f"payslip-extractor-results-{timestamp}.zip"


def create_results_zip(zip_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_file():
                archive.write(path, arcname=path.name)
