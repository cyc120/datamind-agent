"""Bounded, native-text PDF preparation. Nothing is indexed or published here.

Cooperating workers take an advisory lock on a file under `locks/`. A private
temporary directory holds all products; create-if-absent hard links copy them
into an attempt, and an atomic completion-marker link seals the attempt last.
Incomplete or altered attempts are never repaired or overwritten. The storage
root is a trusted local filesystem, not a signed provenance or authority store.

Two platform paths, one contract. On POSIX every path is walked component by
component through directory descriptors with `O_NOFOLLOW`, so no component is
ever resolved through a symlink and every later operation is relative to that
descriptor. Windows has no `dir_fd`, no `O_NOFOLLOW` and no `fcntl`, so handles
there are absolute paths and the same operations are performed by full path.
That port drops two guarantees and keeps the rest:

  * the symlink race defence becomes a best-effort check (`_assert_no_link_walk`
    rejects a symlinked component before use) instead of an atomic `O_NOFOLLOW`
    open. A concurrent attacker who swaps a component for a symlink between the
    check and the use can still redirect the operation. POSIX keeps the atomic
    guarantee; on Windows the storage root must therefore be a directory no
    untrusted party can write to, which the documented deployment already
    requires.
  * `os.fsync` on a directory is unavailable, so `_sync_dir` is a no-op and
    durability of the directory entry relies on the filesystem itself.

The lock is `fcntl.flock` on POSIX and `msvcrt.locking` on Windows; both are
advisory and released when the descriptor closes.

These admission limits are not a hard native-library CPU/memory sandbox. An
isolated worker process with a deadline is required before accepting production
input. OCR, table reconstruction and formula recognition are deliberately absent.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import threading
import time
from types import MappingProxyType
from typing import Annotated, Final
import uuid

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_POSIX = os.name == "posix"
if _POSIX:
    import fcntl
else:  # Windows: advisory locking and byte-range locking are the only option.
    import msvcrt


PARSER_VERSION: Final = f"pdf-native-v1-pymupdf-{pymupdf.VersionBind}"
SCHEMA_VERSION: Final = "pdf-prepared-v1"
MAX_PDF_BYTES: Final = 8 * 1024 * 1024
MAX_PAGES: Final = 30
RENDER_DPI: Final = 110
MAX_PAGE_PIXELS: Final = 4_000_000
MAX_TOTAL_PIXELS: Final = 32_000_000
MAX_TEXT_BLOCKS: Final = 2_000
MAX_TEXT_BYTES: Final = 200_000
MAX_CHUNK_CHARS: Final = 800
MIN_NATIVE_PAGE_CHARS: Final = 30
MAX_EMBEDDED_IMAGES: Final = 128
MAX_EMBEDDED_IMAGE_PIXELS: Final = 16_000_000
MAX_EMBEDDED_TOTAL_PIXELS: Final = 64_000_000
MAX_MANIFEST_BYTES: Final = 64 * 1024
MAX_BUNDLE_BYTES: Final = 4 * 1024 * 1024
MAX_IMAGE_BYTES: Final = 17 * 1024 * 1024
MAX_COMPLETE_BYTES: Final = 2048
MAX_CHUNKS: Final = MAX_TEXT_BLOCKS + math.ceil(MAX_TEXT_BYTES / MAX_CHUNK_CHARS)
LIMITS: Final = MappingProxyType({
    "pdf_bytes": MAX_PDF_BYTES, "pages": MAX_PAGES, "render_dpi": RENDER_DPI,
    "page_pixels": MAX_PAGE_PIXELS, "total_pixels": MAX_TOTAL_PIXELS,
    "text_blocks": MAX_TEXT_BLOCKS, "text_utf8_bytes": MAX_TEXT_BYTES,
    "chunk_chars": MAX_CHUNK_CHARS, "native_page_nonspace_chars": MIN_NATIVE_PAGE_CHARS,
    "embedded_images": MAX_EMBEDDED_IMAGES,
    "embedded_image_pixels": MAX_EMBEDDED_IMAGE_PIXELS,
    "embedded_total_pixels": MAX_EMBEDDED_TOTAL_PIXELS,
    "manifest_bytes": MAX_MANIFEST_BYTES, "bundle_bytes": MAX_BUNDLE_BYTES,
    "page_image_bytes": MAX_IMAGE_BYTES, "completion_marker_bytes": MAX_COMPLETE_BYTES,
    "chunks": MAX_CHUNKS,
    "image_count_policy": "conservative_unique_resource_and_display_occurrence_sum",
})
BOUNDARIES: Final = MappingProxyType({
    "prepare_only": True, "indexed": False, "knowledge_visible": False,
    "ocr_executed": False, "model_calls": 0,
})
WARNINGS: Final = (
    "NATIVE_TEXT_ONLY", "TABLE_STRUCTURE_NOT_RECOVERED",
    "FORMULA_RECOGNITION_NOT_EXECUTED", "IMAGE_AND_VECTOR_CONTENT_NOT_INTERPRETED",
    "NO_HARD_NATIVE_CPU_OR_MEMORY_ISOLATION",
)
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
# Windows defines neither flag. O_NONBLOCK only guards against blocking in open()
# on a FIFO; Windows has no FIFOs reachable through os.open on a regular path, and
# the fstat-before-read ordering below is what actually enforces the size gate.
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


# ── platform primitives ───────────────────────────────────────────────────────
# A "handle" names an open directory. On POSIX it is an int file descriptor and
# every operation below is relative to it; on Windows it is an absolute path and
# the operation is performed on the joined path. Callers never branch: they only
# ever pass a handle back into these functions.

def _assert_no_link_walk(path: Path):
    """Windows-only best-effort stand-in for `O_NOFOLLOW` on every component."""
    if _POSIX:
        return
    parts = Path(os.path.abspath(os.fspath(path))).parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if os.path.islink(current):
            raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")


def _open_at(handle, name: str, flags: int, mode: int = 0o600) -> int:
    """Open `name` inside `handle`. Returns a real file descriptor on both platforms."""
    if _POSIX:
        return os.open(name, flags, mode, dir_fd=handle)
    path = os.path.join(handle, name)
    # Windows cannot express O_NOFOLLOW; check before opening instead.
    if os.path.islink(path):
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
    return os.open(path, flags & ~(_NOFOLLOW | _DIRECTORY), mode)


def _mkdir_at(handle, name: str, mode: int = 0o700):
    if _POSIX:
        return os.mkdir(name, mode, dir_fd=handle)
    return os.mkdir(os.path.join(handle, name), mode)


def _stat_at(handle, name: str):
    if _POSIX:
        return os.stat(name, dir_fd=handle, follow_symlinks=False)
    return os.lstat(os.path.join(handle, name))


def _unlink_at(handle, name: str):
    if _POSIX:
        return os.unlink(name, dir_fd=handle)
    return os.unlink(os.path.join(handle, name))


def _link_at(src_handle, dst_handle, name: str, dst_name: str | None = None):
    """Create-if-absent hard link; never follows a symlink at either end."""
    if _POSIX:
        return os.link(name, dst_name or name, src_dir_fd=src_handle,
                       dst_dir_fd=dst_handle, follow_symlinks=False)
    source = os.path.join(src_handle, name)
    target = os.path.join(dst_handle, dst_name or name)
    if os.path.islink(source) or os.path.islink(target):
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
    return os.link(source, target)


def _rmtree_at(handle, name: str):
    if _POSIX:
        return shutil.rmtree(name, dir_fd=handle)
    return shutil.rmtree(os.path.join(handle, name))


def _sync_dir(handle):
    """Persist a directory entry. Windows cannot fsync a directory; POSIX does."""
    if _POSIX:
        os.fsync(handle)


def _fchmod(fd: int, mode: int):
    if _POSIX:
        os.fchmod(fd, mode)


def _flock(fd: int, *, shared: bool):
    """Advisory whole-file lock, blocking until acquired.

    `msvcrt.locking` locks a byte range and needs the range to exist, so the
    lock file is created with one byte (`_lock` opens it for read-write and the
    range is locked from offset 0 for 1 byte). `LK_LOCK` retries once per second
    for ~10 seconds then raises; the retry loop below keeps the POSIX blocking
    semantics instead of surfacing that spurious timeout.
    """
    if _POSIX:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        return
    mode = msvcrt.LK_RLCK if shared else msvcrt.LK_LOCK
    os.lseek(fd, 0, os.SEEK_SET)
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(fd, mode, 1)
            return
        except OSError:
            time.sleep(0.05)


class PdfPrepareError(ValueError):
    """Stable error code; no source content or private path in the message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PdfPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=64)]
    document_version: Annotated[int, Field(ge=1)]
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    job_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)]
    attempt_token: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)]


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _request(value: dict) -> PdfPrepareRequest:
    try:
        return PdfPrepareRequest.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        raise PdfPrepareError("PDF_REQUEST_INVALID") from exc


def _request_digest(request: PdfPrepareRequest) -> str:
    return _sha(_canonical({"request": request.model_dump(), "parser_version": PARSER_VERSION,
                            "schema_version": SCHEMA_VERSION, "limits": dict(LIMITS)}))


def _identifier(kind: str, request: PdfPrepareRequest, page: int, order: int,
                text: str) -> str:
    return _sha(_canonical({"kind": kind, "document_id": request.document_id,
                            "document_version": request.document_version,
                            "source_sha256": request.source_sha256,
                            "parser_version": PARSER_VERSION, "page_no": page,
                            "reading_order": order, "text": text}))


@contextmanager
def _open_directory(path: Path, *, create: bool = False, code="PDF_STORAGE_PATH_INVALID"):
    """Walk absolute paths with directory descriptors, never resolving symlinks."""
    # Reject traversal before abspath can normalize it away.
    if ".." in Path(path).parts:
        raise PdfPrepareError(code)
    path = Path(os.path.abspath(os.fspath(path)))
    if not _POSIX:
        # Windows: no dir_fd. Validate the whole chain up front, then hand the
        # absolute path back as the handle (see the module docstring for the
        # symlink guarantee this gives up).
        try:
            if create:
                os.makedirs(path, exist_ok=True)
            _assert_no_link_walk(path)
            if not os.path.isdir(path):
                raise PdfPrepareError(code)
        except OSError as exc:
            raise PdfPrepareError(code) from exc
        yield os.fspath(path)
        return
    fd = os.open("/", os.O_RDONLY | _DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError as exc:
        raise PdfPrepareError(code) from exc
    finally:
        os.close(fd)


@contextmanager
def _directory(parent, name: str, *, create=False):
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
    if not _POSIX:
        path = os.path.join(parent, name)
        try:
            if create:
                try:
                    os.mkdir(path, 0o700)
                except FileExistsError:
                    pass
            if os.path.islink(path) or not os.path.isdir(path):
                raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
        except OSError as exc:
            raise PdfPrepareError("PDF_STORAGE_PATH_INVALID") from exc
        yield path
        return
    fd = None
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent)
        yield fd
    except OSError as exc:
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read_file(parent, name: str, limit: int, *, missing="PDF_ARTIFACT_MISSING",
               invalid="PDF_ARTIFACT_INVALID", too_large="PDF_ARTIFACT_TOO_LARGE") -> bytes:
    fd = None
    try:
        # fstat must be reached before consuming input; a FIFO with no writer
        # would otherwise block in open() before the regular-file gate.
        fd = _open_at(parent, name, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PdfPrepareError(invalid)
        if info.st_size > limit:
            raise PdfPrepareError(too_large)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise PdfPrepareError(too_large)
        if len(data) != info.st_size:
            raise PdfPrepareError(invalid)
        return data
    except FileNotFoundError as exc:
        raise PdfPrepareError(missing) from exc
    except OSError as exc:
        raise PdfPrepareError(invalid) from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read_source(source: Path) -> bytes:
    source = Path(source)
    if ".." in source.parts:
        raise PdfPrepareError("PDF_SOURCE_PATH_INVALID")
    with _open_directory(source.absolute().parent, code="PDF_SOURCE_PATH_INVALID") as parent:
        data = _read_file(parent, source.name, MAX_PDF_BYTES,
                          missing="PDF_SOURCE_UNREADABLE", invalid="PDF_SOURCE_PATH_INVALID",
                          too_large="PDF_SOURCE_TOO_LARGE")
    if not data.startswith(b"%PDF-"):
        raise PdfPrepareError("PDF_SOURCE_NOT_PDF")
    return data


@contextmanager
def _lock(root_fd, name: str, *, shared=False):
    with _directory(root_fd, "locks", create=True) as locks:
        fd = None
        try:
            # Separate exclusive creation from opening an existing file. In
            # particular, concurrent macOS O_CREAT|O_NOFOLLOW opens can report
            # ENOENT while another process creates the same directory entry.
            try:
                fd = _open_at(locks, name + ".lock",
                              os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
            except FileExistsError:
                fd = _open_at(locks, name + ".lock", os.O_RDWR | _NOFOLLOW, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
            _flock(fd, shared=shared)
            yield
        except OSError as exc:
            raise PdfPrepareError("PDF_STORAGE_PATH_INVALID") from exc
        finally:
            if fd is not None:
                os.close(fd)  # Closing releases the advisory lock.


def _attempt_lock_name(job_id: str, attempt_token: str) -> str:
    return "attempt-" + _sha(_canonical([job_id, attempt_token]))


def _boundary(function):
    @wraps(function)
    def bounded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PdfPrepareError:
            raise
        except OSError as exc:
            raise PdfPrepareError("PDF_STORAGE_WRITE_FAILED") from exc
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise PdfPrepareError("PDF_MANIFEST_INVALID") from exc
    return bounded


def _write_file(parent, name: str, data: bytes):
    fd = _open_at(parent, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        _fchmod(fd, 0o400)
    finally:
        os.close(fd)


def _publish_source(root, data: bytes, sha: str):
    with _lock(root, "source-" + sha), _directory(root, "sources", create=True) as sources:
        name = sha + ".pdf"
        try:
            existing = _read_file(sources, name, MAX_PDF_BYTES)
        except PdfPrepareError as exc:
            if exc.code != "PDF_ARTIFACT_MISSING":
                raise
        else:
            if _sha(existing) != sha or existing != data:
                raise PdfPrepareError("PDF_SOURCE_STORE_TAMPERED")
            return
        temporary = ".source-" + uuid.uuid4().hex
        try:
            _write_file(sources, temporary, data)
            _link_at(sources, sources, temporary, name)
            _sync_dir(sources)
        finally:
            try:
                _unlink_at(sources, temporary)
            except FileNotFoundError:
                pass


def _box(value) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PdfPrepareError("PDF_TEXT_GEOMETRY_INVALID")
    if any(type(v) not in (int, float) for v in value):
        raise PdfPrepareError("PDF_TEXT_GEOMETRY_INVALID")
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result) or result[0] > result[2] or result[1] > result[3]:
        raise PdfPrepareError("PDF_TEXT_GEOMETRY_INVALID")
    return result


def _rendered_box(source_box: list[float], page: dict) -> list[float]:
    """Equivalent to Rect * page.rotation_matrix, in rendered-page points."""
    rotation = page["rotation_degrees"]
    width, height = page["width_points"], page["height_points"]
    matrices = {0: (1, 0, 0, 1, 0, 0), 90: (0, 1, -1, 0, width, 0),
                180: (-1, 0, 0, -1, width, height), 270: (0, -1, 1, 0, 0, height)}
    if type(rotation) is not int or rotation not in matrices:
        raise PdfPrepareError("PDF_PAGE_GEOMETRY_INVALID")
    a, b, c, d, e, f = matrices[rotation]
    x0, y0, x1, y1 = source_box
    points = [(a * x + c * y + e, b * x + d * y + f)
              for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))]
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points)]


def _admit_images(doc, page, page_no: int, resources: set, totals: dict):
    """Conservative metadata budget, including inline images and image masks.

    Resource images/masks are counted once per xref; every displayed image is
    also counted, without hashes or pixel decoding for deduplication. Repeated
    resources can therefore consume the budget more than once by design.
    """
    def admit(width, height):
        if type(width) is not int or type(height) is not int or min(width, height) <= 0:
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
        pixels = width * height
        if pixels > MAX_EMBEDDED_IMAGE_PIXELS:
            raise PdfPrepareError("PDF_EMBEDDED_IMAGE_PIXEL_LIMIT")
        totals["count"] += 1
        totals["pixels"] += pixels
        if totals["count"] > MAX_EMBEDDED_IMAGES:
            raise PdfPrepareError("PDF_EMBEDDED_IMAGE_COUNT_LIMIT")
        if totals["pixels"] > MAX_EMBEDDED_TOTAL_PIXELS:
            raise PdfPrepareError("PDF_EMBEDDED_TOTAL_PIXEL_LIMIT")

    def dimension(xref, name):
        kind, value = doc.xref_get_key(xref, name)
        if kind != "int":
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
        try:
            return int(value)
        except (ValueError, TypeError) as exc:
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID") from exc

    for image in page.get_images(full=True):
        if len(image) < 4:
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
        xref, mask, width, height = image[:4]
        if type(xref) is not int or xref <= 0 or type(mask) is not int or mask < 0:
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
        pending = [(xref, width, height)]
        if mask:
            pending.append((mask, None, None))
        while pending:
            resource, width, height = pending.pop()
            if resource in resources:
                continue
            if width is None:
                width, height = dimension(resource, "Width"), dimension(resource, "Height")
            admit(width, height)
            resources.add(resource)
            for name in ("SMask", "Mask"):
                kind, value = doc.xref_get_key(resource, name)
                if kind == "xref":
                    try:
                        pending.append((int(value.split()[0]), None, None))
                    except (ValueError, TypeError, IndexError) as exc:
                        raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID") from exc
                elif kind != "null" and not (name == "Mask" and kind == "array") and not (kind == "name" and value == "/None"):
                    raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
    # This API includes inline images, which get_images(full=True) omits. Do not
    # enable hashes/xrefs: that would introduce image digest/decode work here.
    for image in page.get_image_info(hashes=False, xrefs=False):
        if not isinstance(image, dict):
            raise PdfPrepareError("PDF_IMAGE_GEOMETRY_INVALID")
        admit(image.get("width"), image.get("height"))


def _extract(doc, request: PdfPrepareRequest, prefix: str) -> dict:
    """Complete admission checks and text extraction before rendering any page."""
    # Empty user passwords can make is_encrypted/needs_pass false even though
    # the PDF still contains an encryption dictionary and owner restrictions.
    if doc.is_encrypted or doc.needs_pass or doc.xref_get_key(-1, "Encrypt")[0] != "null" or (doc.metadata or {}).get("encryption"):
        raise PdfPrepareError("PDF_ENCRYPTED")
    if not 1 <= doc.page_count <= MAX_PAGES:
        raise PdfPrepareError("PDF_PAGE_LIMIT")
    matrix = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    pages, blocks, chunks = [], [], []
    total_pixels, text_bytes = 0, 0
    image_resources, image_totals = set(), {"count": 0, "pixels": 0}
    # Every page and embedded-image geometry is inspected first.
    for page_no in range(1, doc.page_count + 1):
        page = doc[page_no - 1]
        rect = page.rect
        if not all(math.isfinite(v) for v in rect) or rect.width <= 0 or rect.height <= 0:
            raise PdfPrepareError("PDF_PAGE_GEOMETRY_INVALID")
        pixel_rect = (rect * matrix).irect
        width, height = pixel_rect.width, pixel_rect.height
        if width <= 0 or height <= 0 or width * height > MAX_PAGE_PIXELS:
            raise PdfPrepareError("PDF_PAGE_PIXEL_LIMIT")
        total_pixels += width * height
        if total_pixels > MAX_TOTAL_PIXELS:
            raise PdfPrepareError("PDF_TOTAL_PIXEL_LIMIT")
        _admit_images(doc, page, page_no, image_resources, image_totals)
        pages.append({"page_no": page_no, "width_points": rect.width, "height_points": rect.height,
                      "rotation_degrees": page.rotation, "cropbox_bbox": _box(list(page.cropbox)),
                      "mediabox_bbox": _box(list(page.mediabox)),
                      "pixel_width": width, "pixel_height": height,
                      "image_path": f"{prefix}/pages/page-{page_no:04d}.png",
                      "source_block_ids": [], "warnings": list(WARNINGS)})
    for page_info in pages:
        page_no = page_info["page_no"]
        page_native_chars = 0
        for raw in doc[page_no - 1].get_text("blocks", sort=True):
            if len(raw) < 7 or raw[6] != 0:
                continue
            text = raw[4].replace("\r\n", "\n").replace("\r", "\n").strip()
            if not text:
                continue
            page_native_chars += len("".join(text.split()))
            text_bytes += len(text.encode("utf-8"))
            if text_bytes > MAX_TEXT_BYTES:
                raise PdfPrepareError("PDF_TEXT_BYTE_LIMIT")
            if len(blocks) >= MAX_TEXT_BLOCKS:
                raise PdfPrepareError("PDF_TEXT_BLOCK_LIMIT")
            order = len(page_info["source_block_ids"]) + 1
            block_id = _identifier("block", request, page_no, order, text)
            source_box = _box(raw[:4])
            box = _rendered_box(source_box, page_info)
            block = {"block_id": block_id, "page_no": page_no, "reading_order": order,
                     "block_type": "text", "text": text, "text_sha256": _sha(text.encode()),
                     "bbox": box, "source_bbox": source_box}
            blocks.append(block)
            page_info["source_block_ids"].append(block_id)
            for offset in range(0, len(text), MAX_CHUNK_CHARS):
                piece = text[offset:offset + MAX_CHUNK_CHARS]
                chunk_order = offset // MAX_CHUNK_CHARS + 1
                chunk_id = _identifier("chunk:" + block_id, request, page_no, chunk_order, piece)
                chunks.append({"chunk_id": chunk_id, "document_id": request.document_id,
                               "document_version": request.document_version,
                               "source_sha256": request.source_sha256, "parser_version": PARSER_VERSION,
                               "page_no": page_no, "source_block_ids": [block_id],
                               "source_block_char_start": offset, "source_block_char_end": offset + len(piece),
                               "text": piece, "bbox": box})
        if page_native_chars < MIN_NATIVE_PAGE_CHARS:
            raise PdfPrepareError("PDF_OCR_REQUIRED")
    return {"schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
            "bbox_coordinate_system": "rendered-page-points-top-left",
            "source_bbox_coordinate_system": "unrotated-cropped-page-points-top-left",
            "reading_order_basis": "native-text-sort-unrotated-page",
            "document_id": request.document_id, "document_version": request.document_version,
            "source_sha256": request.source_sha256, "source_path": f"sources/{request.source_sha256}.pdf",
            "pages": pages, "blocks": blocks, "chunks": chunks,
            "text_utf8_bytes": text_bytes, "total_pixels": total_pixels,
            "embedded_images": image_totals["count"], "embedded_total_pixels": image_totals["pixels"],
            "warnings": list(WARNINGS), **BOUNDARIES}


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise PdfPrepareError("PDF_MANIFEST_INVALID") from exc


def _check_relative(path: str):
    if not isinstance(path, str) or "\\" in path:
        raise PdfPrepareError("PDF_ARTIFACT_PATH_INVALID")
    value = PurePosixPath(path)
    if value.is_absolute() or path != str(value) or any(p in (".", "..") for p in value.parts):
        raise PdfPrepareError("PDF_ARTIFACT_PATH_INVALID")


def _artifact(root: int, path: str, limit: int) -> bytes:
    _check_relative(path)
    parts = PurePosixPath(path).parts
    if not parts:
        raise PdfPrepareError("PDF_ARTIFACT_PATH_INVALID")
    from contextlib import ExitStack
    with ExitStack() as stack:
        parent = root
        for part in parts[:-1]:
            parent = stack.enter_context(_directory(parent, part))
        return _read_file(parent, parts[-1], limit)


def _verify_bundle(bundle: dict, manifest: dict, request: PdfPrepareRequest):
    """Cross-check locators/IDs/coverage as well as the byte hashes."""
    expected = {"schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
                "bbox_coordinate_system": "rendered-page-points-top-left",
                "source_bbox_coordinate_system": "unrotated-cropped-page-points-top-left",
                "reading_order_basis": "native-text-sort-unrotated-page",
                "document_id": request.document_id, "document_version": request.document_version,
                "source_sha256": request.source_sha256, "source_path": manifest["source_path"], **BOUNDARIES}
    if not isinstance(bundle, dict) or any(type(bundle.get(k)) is not type(v) or bundle.get(k) != v for k, v in expected.items()):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    if set(bundle) != {*expected, "pages", "blocks", "chunks", "text_utf8_bytes", "total_pixels",
                       "embedded_images", "embedded_total_pixels", "warnings"}:
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    pages, blocks, chunks = bundle.get("pages"), bundle.get("blocks"), bundle.get("chunks")
    if not isinstance(pages, list) or not isinstance(blocks, list) or not isinstance(chunks, list):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    if not 1 <= len(pages) <= MAX_PAGES or len(blocks) > MAX_TEXT_BLOCKS or len(chunks) > MAX_CHUNKS:
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    if not all(isinstance(p, dict) for p in pages):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    if [p.get("page_no") for p in pages] != list(range(1, len(pages) + 1)):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    blocks_by_id, grouped, text_bytes, native_counts = {}, {}, 0, {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_type") != "text" or not isinstance(block.get("text"), str):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        if set(block) != {"block_id", "page_no", "reading_order", "block_type", "text", "text_sha256", "bbox", "source_bbox"}:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        page_no, order, text = block.get("page_no"), block.get("reading_order"), block["text"]
        if type(page_no) is not int or page_no not in range(1, len(pages) + 1) or type(order) is not int:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        ids = grouped.setdefault(page_no, [])
        if order != len(ids) + 1 or not text or block.get("block_id") != _identifier("block", request, page_no, order, text):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        if block["block_id"] in blocks_by_id or block.get("text_sha256") != _sha(text.encode()):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        box = _box(block.get("bbox"))
        source_box = _box(block.get("source_bbox"))
        if box != _rendered_box(source_box, pages[page_no - 1]):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        ids.append(block["block_id"])
        blocks_by_id[block["block_id"]] = block
        text_bytes += len(text.encode())
        native_counts[page_no] = native_counts.get(page_no, 0) + len("".join(text.split()))
    expected_chunks = []
    for block in blocks:
        for start in range(0, len(block["text"]), MAX_CHUNK_CHARS):
            text = block["text"][start:start + MAX_CHUNK_CHARS]
            expected_chunks.append({"chunk_id": _identifier("chunk:" + block["block_id"], request, block["page_no"],
                                                            start // MAX_CHUNK_CHARS + 1, text),
                                    "document_id": request.document_id, "document_version": request.document_version,
                                    "source_sha256": request.source_sha256, "parser_version": PARSER_VERSION,
                                    "page_no": block["page_no"], "source_block_ids": [block["block_id"]],
                                    "source_block_char_start": start, "source_block_char_end": start + len(text),
                                    "text": text, "bbox": block["bbox"]})
    if _canonical(chunks) != _canonical(expected_chunks) or text_bytes > MAX_TEXT_BYTES or bundle.get("text_utf8_bytes") != text_bytes:
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    prefix = f"attempts/{request.job_id}/{request.attempt_token}"
    pixels = 0
    for page in pages:
        if set(page) != {"page_no", "width_points", "height_points", "pixel_width", "pixel_height", "image_path", "source_block_ids", "warnings",
                         "rotation_degrees", "cropbox_bbox", "mediabox_bbox"}:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        if type(page.get("rotation_degrees")) is not int or page["rotation_degrees"] not in (0, 90, 180, 270):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        _box(page.get("cropbox_bbox"))
        _box(page.get("mediabox_bbox"))
        page_no = page["page_no"]
        if page.get("source_block_ids") != grouped.get(page_no, []) or native_counts.get(page_no, 0) < MIN_NATIVE_PAGE_CHARS:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        width, height = page.get("pixel_width"), page.get("pixel_height")
        if type(width) is not int or type(height) is not int or min(width, height) <= 0 or width * height > MAX_PAGE_PIXELS:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        for key in ("width_points", "height_points"):
            if type(page.get(key)) not in (int, float) or not math.isfinite(page[key]) or page[key] <= 0:
                raise PdfPrepareError("PDF_BUNDLE_INVALID")
        if page.get("image_path") != f"{prefix}/pages/page-{page_no:04d}.png" or page.get("warnings") != list(WARNINGS):
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
        pixels += width * height
    if pixels > MAX_TOTAL_PIXELS or bundle.get("total_pixels") != pixels or bundle.get("warnings") != list(WARNINGS):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")
    for key, limit in (("embedded_images", MAX_EMBEDDED_IMAGES), ("embedded_total_pixels", MAX_EMBEDDED_TOTAL_PIXELS)):
        if type(bundle.get(key)) is not int or not 0 <= bundle[key] <= limit:
            raise PdfPrepareError("PDF_BUNDLE_INVALID")
    counts = {"page_count": len(pages), "block_count": len(blocks), "chunk_count": len(chunks),
              "text_utf8_bytes": text_bytes, "total_pixels": pixels,
              "embedded_images": bundle["embedded_images"], "embedded_total_pixels": bundle["embedded_total_pixels"]}
    if _canonical(manifest.get("counts")) != _canonical(counts):
        raise PdfPrepareError("PDF_BUNDLE_INVALID")


def _verify(root: int, job_id: str, attempt_token: str) -> dict:
    prefix = f"attempts/{job_id}/{attempt_token}"
    try:
        complete = _json(_artifact(root, prefix + "/.complete.json", MAX_COMPLETE_BYTES))
    except PdfPrepareError as exc:
        if exc.code in ("PDF_ARTIFACT_MISSING", "PDF_STORAGE_PATH_INVALID"):
            raise PdfPrepareError("PDF_ATTEMPT_INCOMPLETE") from exc
        raise
    if not isinstance(complete, dict) or set(complete) != {"schema_version", "request_sha256", "manifest_sha256"}:
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    if complete["schema_version"] != SCHEMA_VERSION or not _SHA.fullmatch(str(complete["manifest_sha256"])):
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    manifest_bytes = _artifact(root, prefix + "/manifest.json", MAX_MANIFEST_BYTES)
    if _sha(manifest_bytes) != complete["manifest_sha256"]:
        raise PdfPrepareError("PDF_MANIFEST_TAMPERED")
    manifest = _json(manifest_bytes)
    if not isinstance(manifest, dict):
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    expected_keys = {"schema_version", "parser_version", "request", "request_sha256", "source_path",
                     "bundle_path", "artifacts", "limits", "warnings", "counts", *BOUNDARIES}
    if set(manifest) != expected_keys or manifest.get("warnings") != list(WARNINGS):
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    request = _request(manifest.get("request"))
    if request.job_id != job_id or request.attempt_token != attempt_token:
        raise PdfPrepareError("PDF_REQUEST_EVIDENCE_INVALID")
    digest = _request_digest(request)
    if manifest.get("request_sha256") != digest or complete["request_sha256"] != digest:
        raise PdfPrepareError("PDF_REQUEST_EVIDENCE_INVALID")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("parser_version") != PARSER_VERSION or manifest.get("limits") != dict(LIMITS):
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    if any(type(manifest.get(k)) is not type(v) or manifest.get(k) != v for k, v in BOUNDARIES.items()):
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    source_path = f"sources/{request.source_sha256}.pdf"
    bundle_path = prefix + "/bundle.json"
    if manifest.get("source_path") != source_path or manifest.get("bundle_path") != bundle_path:
        raise PdfPrepareError("PDF_ARTIFACT_PATH_INVALID")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not 3 <= len(artifacts) <= MAX_PAGES + 2:
        raise PdfPrepareError("PDF_MANIFEST_INVALID")
    expected_paths = [source_path, bundle_path] + [f"{prefix}/pages/page-{i:04d}.png" for i in range(1, len(artifacts) - 1)]
    if [a.get("path") for a in artifacts if isinstance(a, dict)] != expected_paths:
        raise PdfPrepareError("PDF_ARTIFACT_PATH_INVALID")
    bundle, image_dimensions = None, []
    for artifact in artifacts:
        if set(artifact) != {"path", "sha256", "bytes", "kind"} or type(artifact["bytes"]) is not int or artifact["bytes"] <= 0 or not _SHA.fullmatch(str(artifact["sha256"])):
            raise PdfPrepareError("PDF_MANIFEST_INVALID")
        path = artifact["path"]
        kind, limit = ("source", MAX_PDF_BYTES) if path == source_path else ("bundle", MAX_BUNDLE_BYTES) if path == bundle_path else ("page_image", MAX_IMAGE_BYTES)
        if artifact["kind"] != kind or artifact["bytes"] > limit:
            raise PdfPrepareError("PDF_MANIFEST_INVALID")
        data = _artifact(root, path, limit)
        if len(data) != artifact["bytes"] or _sha(data) != artifact["sha256"]:
            raise PdfPrepareError("PDF_ARTIFACT_TAMPERED")
        if kind == "source" and (artifact["sha256"] != request.source_sha256 or not data.startswith(b"%PDF-")):
            raise PdfPrepareError("PDF_REQUEST_EVIDENCE_INVALID")
        if kind == "bundle":
            bundle = _json(data)
        if kind == "page_image":
            if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                raise PdfPrepareError("PDF_PAGE_IMAGE_INVALID")
            image_dimensions.append(struct.unpack(">II", data[16:24]))
    _verify_bundle(bundle, manifest, request)
    if image_dimensions != [(p["pixel_width"], p["pixel_height"]) for p in bundle["pages"]]:
        raise PdfPrepareError("PDF_PAGE_IMAGE_INVALID")
    with _directory(root, "attempts") as attempts, _directory(attempts, job_id) as job, _directory(job, attempt_token) as attempt:
        if set(os.listdir(attempt)) != {"pages", "bundle.json", "manifest.json", ".complete.json"}:
            raise PdfPrepareError("PDF_ATTEMPT_UNEXPECTED_ARTIFACT")
        with _directory(attempt, "pages") as pages:
            if set(os.listdir(pages)) != {f"page-{i:04d}.png" for i in range(1, manifest["counts"]["page_count"] + 1)}:
                raise PdfPrepareError("PDF_ATTEMPT_UNEXPECTED_ARTIFACT")
    # The flags returned to callers are derived constants, after strict validation.
    return {**manifest, **BOUNDARIES, "manifest_sha256": complete["manifest_sha256"], "verified": True}


def _seal(temp, pages, job, attempt_token: str):
    """Link immutable products without overwriting, then publish one marker."""
    try:
        _mkdir_at(job, attempt_token, 0o700)
        with _directory(job, attempt_token) as attempt, _directory(attempt, "pages", create=True) as output_pages:
            for name in os.listdir(pages):
                _link_at(pages, output_pages, name)
            for name in ("bundle.json", "manifest.json"):
                _link_at(temp, attempt, name)
            _sync_dir(output_pages)
            _sync_dir(attempt)
            try:
                _link_at(temp, attempt, ".complete.json")
            except OSError as exc:
                raise PdfPrepareError("PDF_SEAL_FAILED") from exc
            _sync_dir(attempt)
            _sync_dir(job)
    except OSError as exc:
        raise PdfPrepareError("PDF_SEAL_FAILED") from exc


@_boundary
def verify_prepared(storage_root: Path, job_id: str, attempt_token: str) -> dict:
    if not isinstance(job_id, str) or not isinstance(attempt_token, str) or not _ATTEMPT_ID.fullmatch(job_id) or not _ATTEMPT_ID.fullmatch(attempt_token):
        raise PdfPrepareError("PDF_ATTEMPT_ID_INVALID")
    storage_root = Path(storage_root)
    if ".." in storage_root.parts:
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
    with _open_directory(storage_root) as root, _lock(root, _attempt_lock_name(job_id, attempt_token), shared=True):
        return _verify(root, job_id, attempt_token)


@_boundary
def prepare_pdf(source: Path, storage_root: Path, request: dict) -> dict:
    # PyMuPDF is not thread safe. Every concurrent worker needs its own process.
    if threading.current_thread() is not threading.main_thread():
        raise PdfPrepareError("PDF_THREAD_EXECUTION_FORBIDDEN")
    validated = _request(request)
    data = _read_source(source)
    if _sha(data) != validated.source_sha256:
        raise PdfPrepareError("PDF_SOURCE_SHA256_MISMATCH")
    storage_root = Path(storage_root)
    if ".." in storage_root.parts:
        raise PdfPrepareError("PDF_STORAGE_PATH_INVALID")
    prefix = f"attempts/{validated.job_id}/{validated.attempt_token}"
    with _open_directory(storage_root, create=True) as root, _lock(root, _attempt_lock_name(validated.job_id, validated.attempt_token)):
        with _directory(root, "attempts", create=True) as attempts, _directory(attempts, validated.job_id, create=True) as job:
            try:
                _stat_at(job, validated.attempt_token)
            except FileNotFoundError:
                pass
            else:
                existing = _verify(root, validated.job_id, validated.attempt_token)
                if existing["request_sha256"] != _request_digest(validated):
                    raise PdfPrepareError("PDF_ATTEMPT_REUSED")
                return existing
            try:
                doc = pymupdf.open(stream=data, filetype="pdf")
            except Exception as exc:
                raise PdfPrepareError("PDF_OPEN_FAILED") from exc
            with doc:
                try:
                    bundle = _extract(doc, validated, prefix)
                except PdfPrepareError:
                    raise
                except Exception as exc:
                    raise PdfPrepareError("PDF_TEXT_EXTRACTION_FAILED") from exc
                _publish_source(root, data, validated.source_sha256)
                temporary = ".prepare-" + uuid.uuid4().hex
                with _directory(root, "temporary", create=True) as temporaries:
                    _mkdir_at(temporaries, temporary, 0o700)
                    try:
                        with _directory(temporaries, temporary) as temp, _directory(temp, "pages", create=True) as pages:
                            artifacts = [{"path": bundle["source_path"], "sha256": validated.source_sha256,
                                          "bytes": len(data), "kind": "source"}]
                            page_artifacts = []
                            for page in bundle["pages"]:
                                try:
                                    pix = doc[page["page_no"] - 1].get_pixmap(matrix=pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72),
                                                                          colorspace=pymupdf.csRGB, alpha=False)
                                    if (pix.width, pix.height) != (page["pixel_width"], page["pixel_height"]) or pix.width * pix.height > MAX_PAGE_PIXELS:
                                        raise PdfPrepareError("PDF_PAGE_PIXEL_LIMIT")
                                    png = pix.tobytes("png")
                                    del pix
                                except PdfPrepareError:
                                    raise
                                except Exception as exc:
                                    raise PdfPrepareError("PDF_RENDER_FAILED") from exc
                                if len(png) > MAX_IMAGE_BYTES:
                                    raise PdfPrepareError("PDF_IMAGE_BYTE_LIMIT")
                                _write_file(pages, f"page-{page['page_no']:04d}.png", png)
                                page_artifacts.append({"path": page["image_path"], "sha256": _sha(png),
                                                       "bytes": len(png), "kind": "page_image"})
                            bundle_bytes = _canonical(bundle)
                            if len(bundle_bytes) > MAX_BUNDLE_BYTES:
                                raise PdfPrepareError("PDF_BUNDLE_BYTE_LIMIT")
                            _write_file(temp, "bundle.json", bundle_bytes)
                            artifacts.append({"path": prefix + "/bundle.json", "sha256": _sha(bundle_bytes),
                                              "bytes": len(bundle_bytes), "kind": "bundle"})
                            artifacts.extend(page_artifacts)
                            manifest = {"schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
                                        "request": validated.model_dump(), "request_sha256": _request_digest(validated),
                                        "source_path": bundle["source_path"], "bundle_path": prefix + "/bundle.json",
                                        "artifacts": artifacts, "limits": dict(LIMITS), "warnings": list(WARNINGS),
                                        "counts": {"page_count": len(bundle["pages"]), "block_count": len(bundle["blocks"]),
                                                   "chunk_count": len(bundle["chunks"]), "text_utf8_bytes": bundle["text_utf8_bytes"],
                                                   "total_pixels": bundle["total_pixels"], "embedded_images": bundle["embedded_images"],
                                                   "embedded_total_pixels": bundle["embedded_total_pixels"]}, **BOUNDARIES}
                            manifest_bytes = _canonical(manifest)
                            _write_file(temp, "manifest.json", manifest_bytes)
                            complete = {"schema_version": SCHEMA_VERSION, "request_sha256": manifest["request_sha256"],
                                        "manifest_sha256": _sha(manifest_bytes)}
                            _write_file(temp, ".complete.json", _canonical(complete))
                            # Every destination is create-if-absent. The completion marker is linked last.
                            _seal(temp, pages, job, validated.attempt_token)
                    finally:
                        # Safe handle-relative deletion of our randomly named private temporary subtree.
                        _rmtree_at(temporaries, temporary)
            return _verify(root, validated.job_id, validated.attempt_token)


def prepare_json(source, storage_root, request: dict) -> dict:
    """JSON-friendly entry point for the bounded worker.

    Paths arrive as strings from the task-budget child process; everything else keeps
    the exact semantics of `prepare_pdf`, including the main-thread requirement.
    """
    return prepare_pdf(Path(source), Path(storage_root), request)


def verify_json(storage_root, job_id: str, attempt_token: str) -> dict:
    """JSON-friendly entry point for verifying a sealed attempt in a bounded worker."""
    return verify_prepared(Path(storage_root), job_id, attempt_token)
