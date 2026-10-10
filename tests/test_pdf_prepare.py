"""Synthetic boundary/integrity tests; these are not PDF parsing accuracy scores."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

import pymupdf

from ingestion import pdf_prepare as prepared


def _prepare_child(source, storage, request, barrier):
    """Spawn target; each PyMuPDF document stays in its own main-thread process."""
    barrier.wait(timeout=15)
    return prepared.prepare_pdf(Path(source), Path(storage), request)


def _error_child(operation, source, storage, request, queue):
    try:
        if operation == "prepare":
            prepared.prepare_pdf(Path(source), Path(storage), request)
        else:
            prepared.verify_prepared(Path(storage), request["job_id"], request["attempt_token"])
    except prepared.PdfPrepareError as exc:
        queue.put(exc.code)
    else:
        queue.put("unexpected_success")


def _symlinks_usable(directory: Path) -> bool:
    """Whether this process may create a symlink at all.

    Windows refuses `os.symlink` without Developer Mode or elevation (WinError
    1314), so the symlink-race tests cannot build their fixture there. The POSIX
    branch of `pdf_prepare` still enforces them atomically; on Windows that
    guarantee is documented as replaced by a best-effort check.
    """
    probe = directory / ".symlink-probe"
    try:
        probe.symlink_to(directory / ".symlink-target", target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True


class PdfPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS /var is an OS symlink; explicit resolved test roots remain strict.
        self.base = Path(self.temporary.name).resolve()
        self.storage = self.base / "prepared"
        self.source = self.make_pdf("source.pdf")
        self.symlinks_usable = _symlinks_usable(self.base)

    def require_symlinks(self):
        if not self.symlinks_usable:
            self.skipTest("this platform cannot create symlinks without elevation")

    def make_pdf(self, name, *, pages=1, width=300, height=220, text=None,
                 encryption=False, user_password="synthetic-user", rows=None, font_size=8, lineheight=None):
        path = self.base / name
        with pymupdf.open() as doc:
            for index in range(pages):
                page = doc.new_page(width=width, height=height)
                content = rows if rows is not None else (
                    text if text is not None else f"Synthetic native source page {index + 1}: measured value 20.04 mm.")
                options = {"fontsize": font_size}
                if lineheight is not None:
                    options["lineheight"] = lineheight
                if content:
                    page.insert_text((10, 20), content, **options)
            options = {"garbage": 4, "deflate": True}
            if encryption:
                options.update(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               owner_pw="synthetic-owner", user_pw=user_password)
            path.write_bytes(doc.tobytes(**options))
        return path

    def request(self, source=None, **overrides):
        source = source or self.source
        value = {"document_id": "pdf-demo", "document_version": 1,
                 "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                 "job_id": "job-demo", "attempt_token": "attempt-1"}
        value.update(overrides)
        return value

    def prepare(self, source=None, **overrides):
        return prepared.prepare_pdf(source or self.source, self.storage,
                                    self.request(source, **overrides))

    def bundle(self, result):
        return json.loads((self.storage / result["bundle_path"]).read_bytes())

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(prepared.PdfPrepareError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def rewrite_manifest(self, result, mutate):
        path = self.storage / f"attempts/{result['request']['job_id']}/{result['request']['attempt_token']}"
        manifest_file = path / "manifest.json"
        manifest = json.loads(manifest_file.read_bytes())
        mutate(manifest)
        data = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        manifest_file.chmod(0o600)
        manifest_file.write_bytes(data)
        complete_file = path / ".complete.json"
        complete = json.loads(complete_file.read_bytes())
        complete["manifest_sha256"] = hashlib.sha256(data).hexdigest()
        complete_file.chmod(0o600)
        complete_file.write_text(json.dumps(complete))

    def rewrite_bundle(self, result, mutate):
        path = self.storage / result["bundle_path"]
        bundle = json.loads(path.read_bytes())
        mutate(bundle)
        data = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        path.chmod(0o600)
        path.write_bytes(data)

        def update_manifest(manifest):
            item = next(a for a in manifest["artifacts"] if a["kind"] == "bundle")
            item.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))

        self.rewrite_manifest(result, update_manifest)

    def assert_child_error(self, code, *, operation="prepare", source=None, request=None):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=_error_child,
                                  args=(operation, source or self.source, self.storage,
                                        request or self.request(), queue))
        process.start()
        process.join(timeout=3)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
            self.fail("regular-file admission blocked on a FIFO")
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(queue.get(timeout=2), code)
        queue.close()

    def image_pdf(self, name, *, inline=False, mask_kind=None, oversized=True):
        """Actual compressed image resources; no admission-method mock."""
        source = self.base / name
        with pymupdf.open(self.source) as doc:
            page = doc[0]
            width, height = (5000, 4000) if oversized else (8, 8)
            if inline:
                image_data = zlib.compress(bytes((width * height + 7) // 8))
                stream = (f"q\n50 0 0 50 10 60 cm\nBI /W {width} /H {height} /CS /G /BPC 1 /F /Fl ID\n".encode()
                          + image_data + b"\nEI\nQ\n")
                xref = doc.get_new_xref()
                doc.update_object(xref, "<<>>")
                doc.update_stream(xref, stream)
                contents = [*page.get_contents(), xref]
                doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in contents) + "]")
            else:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), True)
                pix.set_pixel(0, 0, (255, 255, 255, 128))
                page.insert_image(pymupdf.Rect(10, 60, 60, 110), pixmap=pix)
                image, mask = page.get_images(full=True)[0][:2]
                doc.xref_set_key(mask, "Width", str(width))
                doc.xref_set_key(mask, "Height", str(height))
                doc.xref_set_key(mask, "BitsPerComponent", "1")
                doc.update_stream(mask, bytes((width * height + 7) // 8), compress=True)
                if mask_kind == "Mask":
                    doc.xref_set_key(mask, "ImageMask", "true")
                    doc.xref_set_key(mask, "ColorSpace", "null")
                    doc.xref_set_key(image, "SMask", "null")
                    doc.xref_set_key(image, "Mask", f"{mask} 0 R")
            source.write_bytes(doc.tobytes(garbage=4, deflate=True))
        return source

    def test_strict_request_and_fixed_limits(self):
        invalid = ({"document_version": True}, {"document_version": "1"}, {"document_version": 0},
                   {"document_id": "../escape"}, {"job_id": "job/name"}, {"attempt_token": "a.b"},
                   {"source_sha256": "A" * 64}, {"extra": "untrusted"}, {"document_id": "a" * 65})
        for change in invalid:
            with self.subTest(change=change):
                self.assert_error("PDF_REQUEST_INVALID", prepared.prepare_pdf, self.source, self.storage,
                                  self.request(**change))
        self.assertFalse(self.storage.exists())
        with self.assertRaises(TypeError):
            prepared.LIMITS["pages"] = 500

    def test_native_preparation_evidence_and_no_legacy_side_effects(self):
        untouched = self.base / "old-store"
        for name in ("document_registry.json", "qdrant.marker", "page_images/pdf-demo/1.png"):
            path = untouched / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old published evidence")
        before = {p.relative_to(untouched).as_posix(): p.read_bytes() for p in untouched.rglob("*") if p.is_file()}
        result = self.prepare()
        self.assertTrue(result["verified"])
        self.assertTrue(result["prepare_only"])
        for flag in ("indexed", "knowledge_visible", "ocr_executed"):
            self.assertIs(result[flag], False)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["request"], self.request())
        self.assertEqual(result["limits"], dict(prepared.LIMITS))
        self.assertEqual(result["source_path"], "sources/" + self.request()["source_sha256"] + ".pdf")
        self.assertEqual(prepared.verify_prepared(self.storage, "job-demo", "attempt-1"), result)
        bundle = self.bundle(result)
        self.assertEqual(bundle["blocks"][0]["page_no"], 1)
        self.assertEqual(bundle["blocks"][0]["reading_order"], 1)
        self.assertEqual(len(bundle["blocks"][0]["bbox"]), 4)
        self.assertEqual(bundle["chunks"][0]["source_block_ids"], [bundle["blocks"][0]["block_id"]])
        self.assertIn("IMAGE_AND_VECTOR_CONTENT_NOT_INTERPRETED", result["warnings"])
        after = {p.relative_to(untouched).as_posix(): p.read_bytes() for p in untouched.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(list((self.storage / "temporary").iterdir()), [])

    def test_two_files_and_attempts_are_concurrent_and_independent(self):
        other = self.make_pdf("other.pdf", text="Second independent synthetic PDF with value 19.92 mm.")
        context = multiprocessing.get_context("spawn")
        with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
            barrier = manager.Barrier(2)
            first = pool.submit(_prepare_child, self.source, self.storage,
                                self.request(document_id="doc-first", attempt_token="attempt-first"), barrier)
            second = pool.submit(_prepare_child, other, self.storage,
                                 self.request(other, document_id="doc-second", attempt_token="attempt-second"), barrier)
            a, b = first.result(timeout=30), second.result(timeout=30)
        self.assertNotEqual(a["source_path"], b["source_path"])
        self.assertNotEqual(a["bundle_path"], b["bundle_path"])
        self.assertTrue(self.bundle(a)["chunks"][0]["text"].startswith("Synthetic"))
        self.assertTrue(self.bundle(b)["chunks"][0]["text"].startswith("Second"))
        self.assertEqual(len(list((self.storage / "sources").iterdir())), 2)

    def test_same_attempt_concurrent_replay_never_rewrites(self):
        context = multiprocessing.get_context("spawn")
        with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
            barrier = manager.Barrier(2)
            a, b = [future.result(timeout=30) for future in (
                pool.submit(_prepare_child, self.source, self.storage, self.request(), barrier),
                pool.submit(_prepare_child, self.source, self.storage, self.request(), barrier))]
        self.assertEqual(a, b)
        evidence = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.storage / "attempts").rglob("*") if p.is_file()}
        self.assertEqual(self.prepare(), a)
        self.assertEqual(evidence, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in evidence})
        self.assertEqual(len(list((self.storage / "sources").iterdir())), 1)

    def test_same_source_distinct_attempts_share_only_immutable_source(self):
        context = multiprocessing.get_context("spawn")
        with context.Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
            barrier = manager.Barrier(2)
            a, b = [future.result(timeout=30) for future in (
                pool.submit(_prepare_child, self.source, self.storage, self.request(attempt_token="one"), barrier),
                pool.submit(_prepare_child, self.source, self.storage, self.request(attempt_token="two"), barrier))]
        self.assertEqual(a["source_path"], b["source_path"])
        self.assertNotEqual(a["bundle_path"], b["bundle_path"])
        self.assertEqual([block["block_id"] for block in self.bundle(a)["blocks"]],
                         [block["block_id"] for block in self.bundle(b)["blocks"]])

    def test_thread_execution_is_rejected_before_source_read_or_storage_write(self):
        with ThreadPoolExecutor(max_workers=1) as pool, \
             patch.object(prepared, "_read_source", side_effect=AssertionError("must not read source")):
            future = pool.submit(prepared.prepare_pdf, self.source, self.storage, self.request())
            self.assert_error("PDF_THREAD_EXECUTION_FORBIDDEN", future.result, timeout=10)
        self.assertFalse(self.storage.exists())

    def test_attempt_id_reuse_rejects_changed_request(self):
        self.prepare()
        for change in ({"document_id": "another"}, {"document_version": 2}):
            with self.subTest(change=change):
                self.assert_error("PDF_ATTEMPT_REUSED", self.prepare, **change)
        other = self.make_pdf("changed.pdf", text="Changed source file cannot reuse a sealed attempt identifier.")
        self.assert_error("PDF_ATTEMPT_REUSED", self.prepare, other)

    def test_version_source_and_document_scoped_block_and_chunk_ids(self):
        original = self.bundle(self.prepare())
        changed_source = self.make_pdf("changed-source.pdf", text="Synthetic changed content with native source value 20.08 mm.")
        variants = [self.bundle(self.prepare(attempt_token="version", document_version=2)),
                    self.bundle(self.prepare(attempt_token="document", document_id="different")),
                    self.bundle(self.prepare(changed_source, attempt_token="source"))]
        for variant in variants:
            self.assertNotEqual(original["blocks"][0]["block_id"], variant["blocks"][0]["block_id"])
            self.assertNotEqual(original["chunks"][0]["chunk_id"], variant["chunks"][0]["chunk_id"])

    def test_chunk_size_offsets_and_source_reconstruction(self):
        source = self.make_pdf("long.pdf", width=600, height=300, rows=["A" * 400] * 6,
                               font_size=2, lineheight=1)
        result = self.prepare(source)
        bundle = self.bundle(result)
        self.assertGreater(len(bundle["chunks"]), 1)
        for block in bundle["blocks"]:
            chunks = [c for c in bundle["chunks"] if c["source_block_ids"] == [block["block_id"]]]
            self.assertEqual("".join(c["text"] for c in chunks), block["text"])
            for chunk in chunks:
                self.assertLessEqual(len(chunk["text"]), 800)
                self.assertEqual(chunk["text"], block["text"][chunk["source_block_char_start"]:chunk["source_block_char_end"]])

    def test_source_hash_mismatch_and_invalid_pdf_fail_before_products(self):
        self.assert_error("PDF_SOURCE_SHA256_MISMATCH", prepared.prepare_pdf, self.source, self.storage,
                          self.request(source_sha256="0" * 64))
        invalid = self.base / "not.pdf"
        invalid.write_bytes(b"not a PDF")
        self.assert_error("PDF_SOURCE_NOT_PDF", self.prepare, invalid)
        invalid.write_bytes(b"%PDF-1.7\nnot a real PDF")
        self.assert_error("PDF_OPEN_FAILED", self.prepare, invalid)
        self.assertFalse((self.storage / "sources").exists())
        self.assertFalse((self.storage / "attempts/job-demo/attempt-1").exists())

    def test_source_read_once_then_opened_as_verified_bytes(self):
        request = self.request()
        original_bytes = self.source.read_bytes()
        real_open = pymupdf.open
        streams = []

        def opening(*args, **kwargs):
            streams.append(kwargs.get("stream"))
            self.source.write_bytes(b"replaced after the bounded verified read")
            return real_open(*args, **kwargs)

        with patch.object(prepared.pymupdf, "open", side_effect=opening):
            result = prepared.prepare_pdf(self.source, self.storage, request)
        self.assertEqual(streams, [original_bytes])
        self.assertEqual((self.storage / result["source_path"]).read_bytes(), original_bytes)
        self.assertTrue(prepared.verify_prepared(self.storage, "job-demo", "attempt-1")["verified"])

    def test_shared_source_store_tamper_is_rejected_and_not_repaired(self):
        result = self.prepare()
        path = self.storage / result["source_path"]
        path.chmod(0o600)
        path.write_bytes(b"%PDF-tampered")
        self.assert_error("PDF_ARTIFACT_TAMPERED", prepared.verify_prepared, self.storage, "job-demo", "attempt-1")
        self.assert_error("PDF_SOURCE_STORE_TAMPERED", self.prepare, attempt_token="next")
        self.assertEqual(path.read_bytes(), b"%PDF-tampered")

    def test_bundle_page_and_manifest_mutation_are_rejected(self):
        for kind in ("bundle", "page_image", "manifest"):
            with self.subTest(kind=kind):
                result = self.prepare(attempt_token=kind)
                relative = (f"attempts/job-demo/{kind}/manifest.json" if kind == "manifest" else
                            next(a["path"] for a in result["artifacts"] if a["kind"] == kind))
                path = self.storage / relative
                path.chmod(0o600)
                path.write_bytes(path.read_bytes() + b"changed")
                self.assert_error("PDF_MANIFEST_TAMPERED" if kind == "manifest" else "PDF_ARTIFACT_TAMPERED",
                                  prepared.verify_prepared, self.storage, "job-demo", kind)

    def test_partial_attempt_and_unexpected_files_fail_closed(self):
        partial = self.storage / "attempts/job-demo/attempt-1"
        partial.mkdir(parents=True)
        (partial / "bundle.json").write_text("{}")
        self.assert_error("PDF_ATTEMPT_INCOMPLETE", self.prepare)
        self.assert_error("PDF_ATTEMPT_INCOMPLETE", prepared.verify_prepared, self.storage, "job-demo", "attempt-1")
        self.assertEqual((partial / "bundle.json").read_text(), "{}")
        result = self.prepare(attempt_token="finished")
        extra = self.storage / "attempts/job-demo/finished/unlisted.txt"
        extra.write_text("unlisted")
        self.assert_error("PDF_ATTEMPT_UNEXPECTED_ARTIFACT", prepared.verify_prepared, self.storage, "job-demo", "finished")
        self.assertEqual(result["counts"]["page_count"], 1)

    def test_declared_publication_flags_and_escaped_paths_are_never_trusted(self):
        result = self.prepare(attempt_token="flags")
        self.rewrite_manifest(result, lambda m: m.update(indexed=True, knowledge_visible=True))
        self.assert_error("PDF_MANIFEST_INVALID", prepared.verify_prepared, self.storage, "job-demo", "flags")
        result = self.prepare(attempt_token="escape")
        self.rewrite_manifest(result, lambda m: m["artifacts"][0].update(path="../../outside.pdf"))
        self.assert_error("PDF_ARTIFACT_PATH_INVALID", prepared.verify_prepared, self.storage, "job-demo", "escape")
        result = self.prepare(attempt_token="authority")
        self.rewrite_manifest(result, lambda m: m.update(authenticated=True))
        self.assert_error("PDF_MANIFEST_INVALID", prepared.verify_prepared, self.storage, "job-demo", "authority")

    def test_storage_source_artifact_symlinks_and_traversal_are_rejected(self):
        self.require_symlinks()
        linked_source = self.base / "linked-source.pdf"
        linked_source.symlink_to(self.source)
        self.assert_error("PDF_SOURCE_PATH_INVALID", self.prepare, linked_source)
        target = self.base / "outside-storage"
        target.mkdir()
        self.storage.symlink_to(target, target_is_directory=True)
        self.assert_error("PDF_STORAGE_PATH_INVALID", self.prepare)
        self.assertEqual(list(target.iterdir()), [])
        self.storage.unlink()
        self.assert_error("PDF_STORAGE_PATH_INVALID", prepared.prepare_pdf, self.source,
                          self.base / "a/../unsafe", self.request())
        result = self.prepare()
        bundle = self.storage / result["bundle_path"]
        copy = self.base / "outside-bundle.json"
        copy.write_bytes(bundle.read_bytes())
        bundle.unlink()
        bundle.symlink_to(copy)
        self.assert_error("PDF_ARTIFACT_INVALID", prepared.verify_prepared, self.storage, "job-demo", "attempt-1")
        for job, token in (("../job", "attempt"), ("job", "a/b"), ("job", "x" * 65)):
            self.assert_error("PDF_ATTEMPT_ID_INVALID", prepared.verify_prepared, self.storage, job, token)

    def test_managed_directory_symlink_is_rejected_without_external_write(self):
        self.require_symlinks()
        outside = self.base / "outside"
        outside.mkdir()
        self.storage.mkdir()
        (self.storage / "sources").symlink_to(outside, target_is_directory=True)
        self.assert_error("PDF_STORAGE_PATH_INVALID", self.prepare)
        self.assertEqual(list(outside.iterdir()), [])

    def test_pdf_byte_limit_precedes_parsing_and_writes(self):
        source = self.base / "oversized.pdf"
        source.write_bytes(b"%PDF-1.7\n" + b"0" * prepared.MAX_PDF_BYTES)
        with patch.object(prepared.pymupdf, "open", side_effect=AssertionError("must not parse")):
            self.assert_error("PDF_SOURCE_TOO_LARGE", self.prepare, source)
        self.assertFalse(self.storage.exists())

    def test_page_count_and_geometry_limits_precede_rendering(self):
        many = self.make_pdf("31-pages.pdf", pages=31)
        large = self.make_pdf("large-page.pdf", width=2000, height=2000)
        cumulative = self.make_pdf("many-pixels.pdf", pages=30, width=600, height=800)
        for source, code in ((many, "PDF_PAGE_LIMIT"), (large, "PDF_PAGE_PIXEL_LIMIT"),
                             (cumulative, "PDF_TOTAL_PIXEL_LIMIT")):
            with self.subTest(code=code), patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
                self.assert_error(code, self.prepare, source)
        self.assertFalse((self.storage / "sources").exists())

    def test_text_utf8_byte_limit_uses_extracted_native_content_before_render(self):
        source = self.make_pdf("too-much-text.pdf", width=600, height=800,
                               rows=["é" * 300] * 400, font_size=1, lineheight=1)
        with pymupdf.open(source) as doc:
            self.assertLess(len(doc[0].get_text()), prepared.MAX_TEXT_BYTES)
            self.assertGreater(len(doc[0].get_text().encode()), prepared.MAX_TEXT_BYTES)
        with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
            self.assert_error("PDF_TEXT_BYTE_LIMIT", self.prepare, source)
        self.assertFalse((self.storage / "sources").exists())

    def test_native_block_limit_before_render(self):
        source = self.base / "too-many-blocks.pdf"
        with pymupdf.open() as doc:
            for index in range(9):
                page = doc.new_page(width=600, height=800)
                for row in range(250):
                    page.insert_text((10, 10 + row * 2.5), f"Independent source sentence number {index}-{row:04d}.", fontsize=1)
            self.assertEqual(sum(len(p.get_text("blocks", sort=True)) for p in doc), 2250)
            source.write_bytes(doc.tobytes(garbage=4, deflate=True))
        with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
            self.assert_error("PDF_TEXT_BLOCK_LIMIT", self.prepare, source)

    def test_embedded_image_admission_limits_before_render(self):
        cases = [([(1, 0, 5000, 4000)], "PDF_EMBEDDED_IMAGE_PIXEL_LIMIT"),
                 ([(i, 0, 1, 1) for i in range(1, 130)], "PDF_EMBEDDED_IMAGE_COUNT_LIMIT"),
                 ([(i, 0, 4000, 4000) for i in range(1, 6)], "PDF_EMBEDDED_TOTAL_PIXEL_LIMIT")]
        real_get_key = pymupdf.Document.xref_get_key

        def mask_key(doc, xref, key):
            return ("null", "null") if key in ("SMask", "Mask") else real_get_key(doc, xref, key)

        # These three mocks isolate numeric admission arithmetic. Separate tests
        # below use real inline and mask PDFs to verify actual image coverage.
        for images, code in cases:
            with self.subTest(code=code), patch.object(pymupdf.Page, "get_images", return_value=images), \
                 patch.object(pymupdf.Document, "xref_get_key", mask_key), \
                 patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
                self.assert_error(code, self.prepare)

    def test_real_inline_and_image_masks_reject_huge_geometry_before_render(self):
        sources = [self.image_pdf("inline.pdf", inline=True),
                   self.image_pdf("soft-mask.pdf", mask_kind="SMask"),
                   self.image_pdf("explicit-mask.pdf", mask_kind="Mask")]
        with pymupdf.open(sources[0]) as doc:
            self.assertEqual(doc[0].get_images(full=True), [])
            self.assertEqual(doc[0].get_image_info(hashes=False, xrefs=False)[0]["width"], 5000)
        for source in sources:
            with self.subTest(name=source.name), patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
                self.assert_error("PDF_EMBEDDED_IMAGE_PIXEL_LIMIT", self.prepare, source)
        self.assertFalse((self.storage / "sources").exists())

    def test_small_real_inline_image_stays_uninterpreted_and_is_budgeted(self):
        source = self.image_pdf("small-inline.pdf", inline=True, oversized=False)
        result = self.prepare(source)
        self.assertEqual(result["counts"]["embedded_images"], 1)
        self.assertEqual(result["counts"]["embedded_total_pixels"], 64)
        self.assertEqual(result["limits"]["image_count_policy"], "conservative_unique_resource_and_display_occurrence_sum")
        self.assertIn("IMAGE_AND_VECTOR_CONTENT_NOT_INTERPRETED", result["warnings"])

    def test_encrypted_and_scanned_pages_never_run_ocr_or_render(self):
        encrypted = self.make_pdf("encrypted.pdf", encryption=True)
        scan = self.make_pdf("scan.pdf", text="")
        mixed = self.make_pdf("mixed.pdf", pages=2)
        with pymupdf.open(mixed) as doc:
            doc[1].add_redact_annot(doc[1].rect)
            doc[1].apply_redactions()
            doc.save(self.base / "mixed-scan.pdf", garbage=4, deflate=True)
        for source, code in ((encrypted, "PDF_ENCRYPTED"), (scan, "PDF_OCR_REQUIRED"),
                             (self.base / "mixed-scan.pdf", "PDF_OCR_REQUIRED")):
            with self.subTest(code=code), patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("must not render")):
                self.assert_error(code, self.prepare, source)
        self.assertFalse((self.storage / "sources").exists())

    def test_failed_render_does_not_seal_partial_attempt(self):
        with patch.object(pymupdf.Page, "get_pixmap", side_effect=RuntimeError("synthetic render failure")):
            self.assert_error("PDF_RENDER_FAILED", self.prepare)
        self.assertFalse((self.storage / "attempts/job-demo/attempt-1").exists())
        self.assertEqual(list((self.storage / "temporary").iterdir()), [])
        self.assertTrue(self.prepare()["verified"])

    def test_owner_only_encryption_with_empty_user_password_is_rejected(self):
        source = self.make_pdf("owner-only.pdf", encryption=True, user_password="")
        with pymupdf.open(source) as doc:
            self.assertFalse(doc.is_encrypted)
            self.assertFalse(doc.needs_pass)
            self.assertTrue(doc.metadata["encryption"])
        self.assert_error("PDF_ENCRYPTED", self.prepare, source)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFOs do not exist on this platform")
    def test_source_and_artifact_fifos_fail_without_blocking(self):
        source_fifo = self.base / "source-fifo.pdf"
        os.mkfifo(source_fifo)
        self.assert_child_error("PDF_SOURCE_PATH_INVALID", source=source_fifo,
                                request=self.request(source_sha256="0" * 64))
        self.assertFalse(self.storage.exists())
        result = self.prepare()
        bundle = self.storage / result["bundle_path"]
        bundle.unlink()
        os.mkfifo(bundle)
        self.assert_child_error("PDF_ARTIFACT_INVALID", operation="verify")

    def test_rotated_and_cropped_page_bboxes_match_rendered_coordinates(self):
        for rotation in (0, 90, 180, 270):
            for cropped in (False, True):
                with self.subTest(rotation=rotation, cropped=cropped):
                    source = self.base / f"rotation-{rotation}-{cropped}.pdf"
                    with pymupdf.open(self.source) as doc:
                        page = doc[0]
                        if cropped:
                            page.set_cropbox(pymupdf.Rect(5, 5, 290, 210))
                        page.set_rotation(rotation)
                        source.write_bytes(doc.tobytes(garbage=4, deflate=True))
                    result = self.prepare(source, attempt_token=f"r{rotation}-c{cropped}")
                    bundle = self.bundle(result)
                    self.assertEqual(bundle["bbox_coordinate_system"], "rendered-page-points-top-left")
                    self.assertEqual(bundle["pages"][0]["rotation_degrees"], rotation)
                    with pymupdf.open(source) as doc:
                        raw = doc[0].get_text("blocks", sort=True)[0]
                        rendered = list(pymupdf.Rect(raw[:4]) * doc[0].rotation_matrix)
                        self.assertEqual(bundle["blocks"][0]["source_bbox"], list(raw[:4]))
                        for actual, expected in zip(bundle["blocks"][0]["bbox"], rendered):
                            self.assertAlmostEqual(actual, expected, places=4)
                    self.assertEqual(bundle["chunks"][0]["bbox"], bundle["blocks"][0]["bbox"])

    def test_inconsistent_bundle_evidence_rejects_even_with_recomputed_file_hashes(self):
        cases = [("chunk", lambda b: b["chunks"][0].update(chunk_id="0" * 64)),
                 ("bbox", lambda b: b["blocks"][0].update(bbox=[0, 0, 1, 1])),
                 ("field", lambda b: b.update(authenticated=True)),
                 ("page", lambda b: b.update(pages=[123]))]
        for token, mutate in cases:
            with self.subTest(token=token):
                result = self.prepare(attempt_token=token)
                self.rewrite_bundle(result, mutate)
                self.assert_error("PDF_BUNDLE_INVALID", prepared.verify_prepared, self.storage, "job-demo", token)

    def test_interrupted_atomic_seal_keeps_attempt_incomplete_and_never_overwrites(self):
        # Patch the module's own link primitive rather than `os.link`: on Windows
        # `_link_at` joins the handle into a full path before calling `os.link`, so
        # matching on the bare source name would silently stop intercepting.
        real_link_at = prepared._link_at

        def linking(src_handle, dst_handle, name, dst_name=None):
            if name == ".complete.json":
                raise OSError("synthetic seal interruption")
            return real_link_at(src_handle, dst_handle, name, dst_name)

        with patch.object(prepared, "_link_at", side_effect=linking):
            self.assert_error("PDF_SEAL_FAILED", self.prepare)
        attempt = self.storage / "attempts/job-demo/attempt-1"
        self.assertFalse((attempt / ".complete.json").exists())
        before = {p: p.read_bytes() for p in attempt.rglob("*") if p.is_file()}
        self.assert_error("PDF_ATTEMPT_INCOMPLETE", prepared.verify_prepared, self.storage, "job-demo", "attempt-1")
        self.assert_error("PDF_ATTEMPT_INCOMPLETE", self.prepare)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(list((self.storage / "temporary").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
