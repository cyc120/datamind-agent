"""Bounded local staging of raw DXF/JSON bytes; parsing remains a separate job."""
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
LIMITS={'.dxf':2*1024*1024,'.json':1024*1024}


def publish(data,filename,provenance,dsn,identity):
    suffix=Path(filename).suffix.lower()
    if (suffix not in LIMITS or Path(filename).name!=filename or not filename or len(filename)>200
            or any(ord(c)<32 for c in filename) or '\\' in filename):fail('FILE_TYPE_OR_NAME_INVALID',422)
    if not data or len(data)>LIMITS[suffix]:fail('FILE_UPLOAD_TOO_LARGE_OR_EMPTY',413)
    kind='drawing' if suffix=='.dxf' else 'measurement'
    directory=ROOT/'data'/('cad_demo' if kind=='drawing' else 'dimension_demo')/('development' if provenance=='synthetic_demo' else 'authorized')
    if not directory.resolve().is_relative_to(ROOT.resolve()):fail('FILE_STAGING_DIRECTORY_INVALID')
    sha=hashlib.sha256(data).hexdigest();target=directory/('upload-'+sha+suffix)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        lock_identity(cur,identity,'admin')
        directory.mkdir(parents=True,exist_ok=True)
        tmp=directory/('.upload-'+uuid.uuid4().hex+'.tmp')
        try:
            with tmp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            try:os.link(tmp,target)
            except FileExistsError:pass
        finally:tmp.unlink(missing_ok=True)
        if (not target.resolve().is_relative_to(directory.resolve()) or not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest()!=sha):fail('FILE_STAGED_BYTES_CHANGED')
    return {'type':kind,'name':filename,'source_path':str(target.relative_to(ROOT)),
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
