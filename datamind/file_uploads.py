"""Bounded local staging of raw upload bytes; parsing remains a separate job.

Two staging surfaces share one write path and stay deliberately different:

* `publish`/`stage` are the legacy `/v1/files/uploads` path. Its bounds (`.dxf` 2 MiB,
  `.json` 1 MiB) and its `FILE_UPLOAD_TOO_LARGE_OR_EMPTY` error code are frozen by the
  existing regression tests, so `LIMITS` is unchanged.
* `publish_upload`/`stage_upload` are the `/v1/uploads` pipeline's surface with the
  interface's own bounds (`.dxf` 8 MiB, `.pdf` 32 MiB, Office 16 MiB) and its own stable
  codes (`UPLOAD_TOO_LARGE`, `UPLOAD_EMPTY`). Office/PDF bytes are staged under
  `data/uploads/<provenance>`; DXF and measurement JSON keep their established
  `data/cad_demo` and `data/dimension_demo` roots so the CAD staging guards still accept
  them by path.

Both write content-addressed `upload-<sha256><suffix>` files with a hard link, so a
re-upload is a no-op and a half-written temporary file never becomes visible.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from .auth import lock_identity
from .cad_store import ROOT,fail

UPLOAD_SLOTS=asyncio.Semaphore(4)
#: Legacy `/v1/files/uploads` bounds; frozen by tests/test_file_uploads.py.
LIMITS={'.dxf':2*1024*1024,'.json':1024*1024}
#: `/v1/uploads` bounds (frozen interface): the same suffixes, larger ceilings.
UPLOAD_LIMITS={'.dxf':8*1024*1024,'.json':1024*1024,'.docx':16*1024*1024,
               '.pptx':16*1024*1024,'.xlsx':16*1024*1024,'.pdf':32*1024*1024}
#: suffix -> (directory under data/, legacy job kind). Office and PDF share one root.
DIRECTORIES={'.dxf':('cad_demo','drawing'),'.json':('dimension_demo','measurement'),
             '.docx':('uploads','document'),'.pptx':('uploads','document'),
             '.xlsx':('uploads','document'),'.pdf':('uploads','document')}


def staged_name_valid(filename):
    """A staged name is a bare file name: no path parts, no control bytes, bounded length."""
    return (isinstance(filename,str) and bool(filename) and len(filename)<=200
            and Path(filename).name==filename and '\\' not in filename
            and not any(ord(c)<32 for c in filename))


def _write(data,directory,sha,suffix):
    """Link the bytes into place under `directory` and verify the stored content."""
    if not directory.resolve().is_relative_to(ROOT.resolve()):fail('FILE_STAGING_DIRECTORY_INVALID')
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/('upload-'+sha+suffix)
    tmp=directory/('.upload-'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        try:os.link(tmp,target)
        except FileExistsError:pass
    finally:tmp.unlink(missing_ok=True)
    if (not target.resolve().is_relative_to(directory.resolve()) or not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest()!=sha):fail('FILE_STAGED_BYTES_CHANGED')
    return target


def publish(data,filename,provenance,dsn,identity):
    suffix=Path(filename).suffix.lower()
    if (suffix not in LIMITS or Path(filename).name!=filename or not filename or len(filename)>200
            or any(ord(c)<32 for c in filename) or '\\' in filename):fail('FILE_TYPE_OR_NAME_INVALID',422)
    if not data or len(data)>LIMITS[suffix]:fail('FILE_UPLOAD_TOO_LARGE_OR_EMPTY',413)
    kind='drawing' if suffix=='.dxf' else 'measurement'
    directory=ROOT/'data'/('cad_demo' if kind=='drawing' else 'dimension_demo')/('development' if provenance=='synthetic_demo' else 'authorized')
    sha=hashlib.sha256(data).hexdigest()
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        lock_identity(cur,identity,'admin')
        target=_write(data,directory,sha,suffix)
    return {'type':kind,'name':filename,'source_path':str(target.relative_to(ROOT)),
            'source_sha256':sha,'provenance':provenance,'bytes':len(data)}


def publish_upload(data,filename,provenance,dsn,identity):
    """Stage one `/v1/uploads` body under the interface's bounds and return its identity.

    The caller has already rejected `.dwg` and unsupported suffixes; this function keeps a
    defensive suffix check so a direct call cannot stage something the queue cannot route.
    """
    suffix=Path(filename).suffix.lower()
    if suffix not in UPLOAD_LIMITS or not staged_name_valid(filename):fail('FILE_TYPE_OR_NAME_INVALID',422)
    if not data:fail('UPLOAD_EMPTY',422)
    if len(data)>UPLOAD_LIMITS[suffix]:fail('UPLOAD_TOO_LARGE',413)
    folder,kind=DIRECTORIES[suffix]
    directory=ROOT/'data'/folder/('development' if provenance=='synthetic_demo' else 'authorized')
    sha=hashlib.sha256(data).hexdigest()
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Same admin recheck the legacy staging path performs before touching the disk.
        lock_identity(cur,identity,'admin')
        target=_write(data,directory,sha,suffix)
    return {'type':kind,'name':filename,'suffix':suffix,'source_path':str(target.relative_to(ROOT)),
            'source_sha256':sha,'provenance':provenance,'bytes':len(data)}


async def stage(request,filename,provenance,dsn,identity):
    suffix=Path(filename).suffix.lower()
    if suffix not in LIMITS:fail('FILE_TYPE_OR_NAME_INVALID',422)
    maximum=LIMITS[suffix]
    if request.headers.get('content-length'):
        try:declared=int(request.headers['content-length'])
        except ValueError:fail('FILE_UPLOAD_INVALID_LENGTH',422)
        if declared>maximum:fail('FILE_UPLOAD_TOO_LARGE_OR_EMPTY',413)
    async with UPLOAD_SLOTS:
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>maximum:fail('FILE_UPLOAD_TOO_LARGE_OR_EMPTY',413)
            chunks.append(chunk)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(publish,b''.join(chunks),filename,provenance,dsn,identity)


async def stage_upload(request,filename,provenance,dsn,identity):
    """Stream one `/v1/uploads` body with the interface's per-suffix ceiling.

    The ceiling is enforced while streaming, so an oversized body is refused without ever
    being buffered in full or written to disk.
    """
    suffix=Path(filename).suffix.lower()
    if suffix not in UPLOAD_LIMITS or not staged_name_valid(filename):fail('FILE_TYPE_OR_NAME_INVALID',422)
    maximum=UPLOAD_LIMITS[suffix]
    if request.headers.get('content-length'):
        try:declared=int(request.headers['content-length'])
        except ValueError:fail('UPLOAD_LENGTH_INVALID',422)
        if declared>maximum:fail('UPLOAD_TOO_LARGE',413)
    async with UPLOAD_SLOTS:
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>maximum:fail('UPLOAD_TOO_LARGE',413)
            chunks.append(chunk)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(publish_upload,b''.join(chunks),filename,provenance,dsn,identity)
