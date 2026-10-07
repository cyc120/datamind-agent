"""Immutable private PDF vector plans; no legacy collections or publication writes.

The caller owns authentication, leases and PostgreSQL publication CAS. This
adapter freezes one embedding result and verifies actual Qdrant records. Tests
may inject an explicitly synthetic embedder/client; production only loads local
model files and connects to a separately authenticated loopback Qdrant service.
"""
from contextlib import contextmanager
import copy
from functools import wraps
import hashlib
from importlib.metadata import version as package_version
from itertools import islice
import json
import math
import numbers
import os
from pathlib import Path
import re
import stat
import struct
from urllib.parse import urlsplit
import uuid
import warnings

from fastapi import HTTPException

from ingestion import pdf_prepare as prepared
from . import pdf_publications as snapshots


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'pdf-vector-plan-v1'
VECTOR_DIM = 512
MAX_CHUNKS = 512
MAX_TEXT_BYTES = 200_000
MAX_PLAN_BYTES = 16 * 1024 * 1024
BATCH_SIZE = 32
COLLECTION_PREFIX = 'pdf_private_'
MODEL = 'BAAI/bge-small-zh-v1.5'
SYNTHETIC_MODEL = 'synthetic-pdf-test-v1'
NORMALIZATION = 'l2-f64-to-f32-positive-zero-v1'
POINT_NAMESPACE = uuid.UUID('44169bc5-a505-5303-846b-91738568b08c')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_SHA = re.compile(r'^[a-f0-9]{64}$')
_MODEL_FILES = ('model_optimized.onnx', 'config.json', 'tokenizer.json',
                'special_tokens_map.json', 'tokenizer_config.json')
_PLAN_KEYS = {'schema', 'profile', 'profile_sha256', 'collection', 'publication_id',
              'document_id', 'document_version', 'source_sha256', 'manifest_sha256',
              'registration_sha256', 'chunks', 'points', 'plan_sha256'}
_CHUNK_KEYS = {'chunk_id', 'document_id', 'document_version', 'source_sha256',
               'parser_version', 'page_no', 'source_block_ids', 'source_block_char_start',
               'source_block_char_end', 'text', 'bbox', 'content_sha256'}


def fail(code, status=409):
    raise HTTPException(status, detail={'error_code': code})


def _stable(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            fail('PDF_INDEX_PLAN_INVALID')
    return wrapped


def _canonical(value):
    return prepared._canonical(value)


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _sdk_versions():
    return {name: package_version(name) for name in ('fastembed', 'qdrant-client', 'onnxruntime')}


def _profile(*, synthetic, files=None):
    files = files or {name: hashlib.sha256(('synthetic-fixture:' + name).encode()).hexdigest()
                      for name in _MODEL_FILES}
    return {'model': SYNTHETIC_MODEL if synthetic else MODEL, 'synthetic': synthetic,
            'dimension': VECTOR_DIM, 'distance': 'Dot', 'datatype': 'float32',
            'normalization': NORMALIZATION, 'embedding_text': 'native-chunk-exact-text-v1',
            'sdk_versions': _sdk_versions(), 'model_file_sha256': files['model_optimized.onnx'],
            'model_files_sha256': files}


def _validate_profile(profile):
    keys = {'model', 'synthetic', 'dimension', 'distance', 'datatype', 'normalization',
            'embedding_text', 'sdk_versions', 'model_file_sha256', 'model_files_sha256'}
    if not isinstance(profile, dict) or set(profile) != keys or type(profile['synthetic']) is not bool:
        fail('PDF_INDEX_PROFILE_INVALID')
    files = profile['model_files_sha256']
    if (not isinstance(files, dict) or set(files) != set(_MODEL_FILES)
            or not all(_sha(value) for value in files.values())
            or profile['model_file_sha256'] != files['model_optimized.onnx']):
        fail('PDF_INDEX_PROFILE_INVALID')
    if (profile['model'] != (SYNTHETIC_MODEL if profile['synthetic'] else MODEL)
            or type(profile['dimension']) is not int or profile['dimension'] != VECTOR_DIM
            or profile['distance'] != 'Dot' or profile['datatype'] != 'float32'
            or profile['normalization'] != NORMALIZATION
            or profile['embedding_text'] != 'native-chunk-exact-text-v1'):
        fail('PDF_INDEX_PROFILE_INVALID')
    versions = profile['sdk_versions']
    if (not isinstance(versions, dict) or set(versions) != {'fastembed', 'qdrant-client', 'onnxruntime'}
            or not all(isinstance(value, str) and 1 <= len(value) <= 64 for value in versions.values())):
        fail('PDF_INDEX_PROFILE_INVALID')
    if profile['synthetic'] and profile != _profile(synthetic=True):
        fail('PDF_INDEX_PROFILE_INVALID')


def _model_directory(cache):
    try:
        root = Path(cache).resolve(strict=True)
        if not root.is_dir():
            raise ValueError()
        if (root / _MODEL_FILES[0]).is_file():
            return root, root
        repository = root / 'models--Qdrant--bge-small-zh-v1.5'
        reference = repository / 'refs/main'
        if reference.is_file():
            revision = reference.read_text().strip()
            if not re.fullmatch(r'[a-f0-9]{40,64}', revision):
                raise ValueError()
            candidate = repository / 'snapshots' / revision
        else:
            candidates = [p for p in (repository / 'snapshots').iterdir()
                          if p.is_dir() and (p / _MODEL_FILES[0]).is_file()]
            if len(candidates) != 1:
                raise ValueError()
            candidate = candidates[0]
        candidate = candidate.resolve(strict=True)
        if not candidate.is_relative_to(root):
            raise ValueError()
        return root, candidate
    except (OSError, ValueError):
        fail('PDF_INDEX_MODEL_UNAVAILABLE', 503)


def _model_hashes(root, directory):
    hashes = {}
    try:
        for name in _MODEL_FILES:
            path = (directory / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                maximum = 256 * 1024 * 1024 if name.endswith('.onnx') else 8 * 1024 * 1024
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                    raise ValueError()
                digest = hashlib.sha256()
                total = 0
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        total += len(chunk)
                        if total > maximum:
                            raise ValueError()
                        digest.update(chunk)
                after = os.fstat(fd)
                if total != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError()
                hashes[name] = digest.hexdigest()
            finally:
                os.close(fd)
        return hashes
    except (OSError, ValueError):
        fail('PDF_INDEX_MODEL_UNAVAILABLE', 503)


def _production_embedding(texts):
    root, directory = _model_directory(os.environ.get('DATAMIND_PDF_EMBED_CACHE', ROOT / '.cache/fastembed'))
    hashes = _model_hashes(root, directory)
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from fastembed import TextEmbedding
        # specific_model_path skips HF/GCS discovery entirely; local_files_only
        # additionally prohibits fallback downloads. Existing cache is read only.
        model = TextEmbedding(model_name=MODEL, cache_dir=str(root), threads=2,
                              providers=['CPUExecutionProvider'], local_files_only=True,
                              specific_model_path=str(directory))
        vectors = list(islice(model.embed(texts, batch_size=BATCH_SIZE), len(texts) + 1))
    except Exception:
        fail('PDF_INDEX_MODEL_UNAVAILABLE', 503)
    if hashes != _model_hashes(root, directory):
        fail('PDF_INDEX_MODEL_CHANGED')
    return _profile(synthetic=False, files=hashes), vectors


def _normalize(vector):
    try:
        values = list(islice(iter(vector), VECTOR_DIM + 1))
        if len(values) != VECTOR_DIM or any(isinstance(v, bool) or not isinstance(v, numbers.Real) for v in values):
            raise ValueError()
        values = [float(v) for v in values]
        if not all(math.isfinite(v) for v in values):
            raise ValueError()
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError()
        frozen = [struct.unpack('<f', struct.pack('<f', v / norm))[0] for v in values]
        return [0.0 if value == 0 else value for value in frozen]
    except (ValueError, TypeError, OverflowError):
        fail('PDF_INDEX_VECTOR_INVALID')


def query_embedding(question, profile, embedder=None):
    """Use the exact frozen publication profile; never silently migrate models.

    The optional synthetic embedder is a Python test seam, never an HTTP option.
    Query text uses the same exact-text contract as native chunk embeddings.
    """
    _validate_profile(profile)
    try:
        valid = (isinstance(question, str) and bool(question.strip()) and len(question) <= 2000
                 and len(question.encode('utf-8')) <= 8000)
    except UnicodeError:
        valid = False
    if not valid:
        fail('PDF_SEARCH_QUESTION_INVALID', 422)
    if embedder is None:
        if profile['synthetic']:
            fail('PDF_SEARCH_PRODUCTION_PROFILE_REQUIRED')
        actual_profile, result = _production_embedding([question])
    else:
        actual_profile = _profile(synthetic=True)
        try:
            result = list(islice(iter(embedder([question])), 2))
        except Exception:
            fail('PDF_INDEX_EMBEDDING_FAILED', 503)
    if _canonical(actual_profile) != _canonical(profile):
        fail('PDF_SEARCH_PROFILE_MISMATCH')
    if len(result) != 1:
        fail('PDF_INDEX_VECTOR_COUNT_INVALID')
    return _normalize(result[0])


def _vector_bytes(vector):
    if (not isinstance(vector, list) or len(vector) != VECTOR_DIM
            or any(type(v) is not float or not math.isfinite(v) for v in vector)):
        fail('PDF_INDEX_VECTOR_INVALID')
    try:
        data = struct.pack('<' + 'f' * VECTOR_DIM, *vector)
        values = struct.unpack('<' + 'f' * VECTOR_DIM, data)
    except (OverflowError, struct.error):
        fail('PDF_INDEX_VECTOR_INVALID')
    if (any(v != f or (v == 0 and math.copysign(1, v) < 0) for v, f in zip(vector, values))
            or not 0.99999 <= math.hypot(*values) <= 1.00001):
        fail('PDF_INDEX_VECTOR_INVALID')
    return data


def _bound_chunks(snapshot_row, bundle):
    try:
        row = snapshots._metadata(snapshot_row)
        native = snapshots._snapshot(row['snapshot'])
        prepared._verify_bundle(bundle, row['snapshot'], native)
        artifact = row['snapshot']['artifacts'][1]
        encoded = _canonical(bundle)
        if len(encoded) != artifact['bytes'] or hashlib.sha256(encoded).hexdigest() != artifact['sha256']:
            raise ValueError()
    except (HTTPException, prepared.PdfPrepareError, ValueError, TypeError, KeyError):
        fail('PDF_INDEX_SNAPSHOT_INVALID')
    chunks = [{**copy.deepcopy(chunk), 'content_sha256': hashlib.sha256(chunk['text'].encode()).hexdigest()}
              for chunk in bundle['chunks']]
    if not 1 <= len(chunks) <= MAX_CHUNKS:
        fail('PDF_INDEX_CHUNK_LIMIT', 422)
    if sum(len(chunk['text'].encode()) for chunk in chunks) > MAX_TEXT_BYTES:
        fail('PDF_INDEX_TEXT_LIMIT', 422)
    return row, chunks


def _point_id(plan, chunk):
    identity = [plan['publication_id'], plan['profile_sha256'], chunk['chunk_id'], chunk['content_sha256']]
    return str(uuid.uuid5(POINT_NAMESPACE, _canonical(identity).decode()))


def _payload_for(plan, chunk, vector):
    return {'schema': SCHEMA, 'publication_id': plan['publication_id'],
            'document_id': plan['document_id'], 'document_version': plan['document_version'],
            'source_sha256': plan['source_sha256'], 'manifest_sha256': plan['manifest_sha256'],
            'registration_sha256': plan['registration_sha256'], 'profile_sha256': plan['profile_sha256'],
            'chunk_id': chunk['chunk_id'], 'content_sha256': chunk['content_sha256'],
            'parser_version': chunk['parser_version'], 'page_no': chunk['page_no'],
            'source_block_ids': chunk['source_block_ids'],
            'source_block_char_start': chunk['source_block_char_start'],
            'source_block_char_end': chunk['source_block_char_end'],
            # Qdrant 1.12 JSON can change float64 payload coordinates by one ULP.
            # Preserve exact source coordinates in chunks and encode their bitwise
            # numeric identity as strings for immutable payload verification.
            'bbox_f64_hex': [float(value).hex() for value in chunk['bbox']],
            'vector_sha256': hashlib.sha256(_vector_bytes(vector)).hexdigest(), 'acl': ['admin_private']}


@_stable
def build_plan(snapshot_row, bundle, publication_id, embedder=None):
    if not isinstance(publication_id, str) or not _ID.fullmatch(publication_id):
        fail('PDF_INDEX_PUBLICATION_ID_INVALID', 422)
    row, chunks = _bound_chunks(snapshot_row, bundle)
    texts = [chunk['text'] for chunk in chunks]
    if embedder is None:
        profile, vectors = _production_embedding(texts)
    else:
        profile = _profile(synthetic=True)
        try:
            vectors = list(islice(iter(embedder(texts)), len(texts) + 1))
        except Exception:
            fail('PDF_INDEX_EMBEDDING_FAILED', 503)
    if len(vectors) != len(chunks):
        fail('PDF_INDEX_VECTOR_COUNT_INVALID')
    plan = {'schema': SCHEMA, 'profile': profile, 'profile_sha256': _digest(profile),
            'collection': COLLECTION_PREFIX + _digest(profile), 'publication_id': publication_id,
            **{key: row[key] for key in ('document_id', 'document_version', 'source_sha256',
                                       'manifest_sha256', 'registration_sha256')}, 'chunks': chunks, 'points': []}
    for chunk, vector in zip(chunks, vectors):
        frozen = _normalize(vector)
        plan['points'].append({'id': _point_id(plan, chunk), 'payload': _payload_for(plan, chunk, frozen), 'vector': frozen})
    plan['plan_sha256'] = _digest(plan)
    validate_plan(plan, row, bundle, publication_id)
    return plan


def _checked_plan(plan):
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        fail('PDF_INDEX_PLAN_INVALID')
    if len(_canonical(plan)) > MAX_PLAN_BYTES:
        fail('PDF_INDEX_PLAN_TOO_LARGE', 422)
    _validate_profile(plan['profile'])
    if (plan['schema'] != SCHEMA or plan['profile_sha256'] != _digest(plan['profile'])
            or plan['collection'] != COLLECTION_PREFIX + plan['profile_sha256']
            or not isinstance(plan['publication_id'], str) or not _ID.fullmatch(plan['publication_id'])
            or not isinstance(plan['document_id'], str) or not snapshots._DOC.fullmatch(plan['document_id'])
            or type(plan['document_version']) is not int or not 1 <= plan['document_version'] <= 2_147_483_647
            or any(not _sha(plan[key]) for key in ('source_sha256', 'manifest_sha256', 'registration_sha256', 'plan_sha256'))
            or plan['plan_sha256'] != _digest({k: v for k, v in plan.items() if k != 'plan_sha256'})):
        fail('PDF_INDEX_PLAN_INVALID')
    chunks, points = plan['chunks'], plan['points']
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= MAX_CHUNKS or not isinstance(points, list) or len(points) != len(chunks):
        fail('PDF_INDEX_PLAN_INVALID')
    seen, text_bytes = set(), 0
    for chunk, point in zip(chunks, points):
        if not isinstance(chunk, dict) or set(chunk) != _CHUNK_KEYS:
            fail('PDF_INDEX_PLAN_INVALID')
        if (any(chunk[key] != plan[key] or type(chunk[key]) is not type(plan[key])
                for key in ('document_id', 'document_version', 'source_sha256'))
                or not _sha(chunk['chunk_id']) or chunk['chunk_id'] in seen
                or not isinstance(chunk['text'], str) or not 1 <= len(chunk['text']) <= prepared.MAX_CHUNK_CHARS
                or chunk['content_sha256'] != hashlib.sha256(chunk['text'].encode()).hexdigest()
                or chunk['parser_version'] != prepared.PARSER_VERSION
                or type(chunk['page_no']) is not int or not 1 <= chunk['page_no'] <= prepared.MAX_PAGES
                or not isinstance(chunk['source_block_ids'], list) or len(chunk['source_block_ids']) != 1
                or not _sha(chunk['source_block_ids'][0])
                or type(chunk['source_block_char_start']) is not int or chunk['source_block_char_start'] < 0
                or chunk['source_block_char_start'] % prepared.MAX_CHUNK_CHARS != 0
                or type(chunk['source_block_char_end']) is not int
                or chunk['source_block_char_end'] != chunk['source_block_char_start'] + len(chunk['text'])):
            fail('PDF_INDEX_PLAN_INVALID')
        try:
            prepared._box(chunk['bbox'])
        except prepared.PdfPrepareError:
            fail('PDF_INDEX_PLAN_INVALID')
        seen.add(chunk['chunk_id']); text_bytes += len(chunk['text'].encode())
        if not isinstance(point, dict) or set(point) != {'id', 'payload', 'vector'}:
            fail('PDF_INDEX_PLAN_INVALID')
        _vector_bytes(point['vector'])
        if point['id'] != _point_id(plan, chunk) or _canonical(point['payload']) != _canonical(_payload_for(plan, chunk, point['vector'])):
            fail('PDF_INDEX_PLAN_INVALID')
    if text_bytes > MAX_TEXT_BYTES:
        fail('PDF_INDEX_TEXT_LIMIT', 422)
    return plan


@_stable
def validate_plan(plan, snapshot_row, bundle, publication_id):
    _checked_plan(plan)
    row, chunks = _bound_chunks(snapshot_row, bundle)
    if (publication_id != plan['publication_id'] or _canonical(chunks) != _canonical(plan['chunks'])
            or any(row[key] != plan[key] for key in ('document_id', 'document_version', 'source_sha256',
                                                   'manifest_sha256', 'registration_sha256'))):
        fail('PDF_INDEX_SNAPSHOT_CHANGED')


@_stable
def safe_chunks(plan):
    _checked_plan(plan)
    return copy.deepcopy(plan['chunks'])


def client_factory():
    """Fail closed against legacy, unauthenticated or non-loopback endpoints."""
    from qdrant_client import QdrantClient
    import httpx
    url = os.environ.get('DATAMIND_PDF_QDRANT_URL', '')
    key = os.environ.get('DATAMIND_PDF_QDRANT_API_KEY', '')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'} or parsed.port is None or not 1 <= parsed.port <= 65535
                or not key or len(key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise ValueError()
    except (ValueError, TypeError):
        fail('PDF_INDEX_QDRANT_CONFIG_INVALID', 503)
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as probe:
            response = probe.get(url.rstrip('/') + '/collections')
        if response.status_code not in {401, 403}:
            fail('PDF_INDEX_PRIVATE_QDRANT_REQUIRED', 503)
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Api key is used with an insecure connection.*')
            client = QdrantClient(url=url, api_key=key, timeout=5, trust_env=False)
        try:
            client.get_collections()
        except Exception:
            client.close()
            fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
        return client
    except HTTPException:
        raise
    except Exception:
        fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)


@contextmanager
def _client(client):
    owned = client is None
    client = client_factory() if owned else client
    try:
        yield client
    finally:
        if owned:
            client.close()


def _already_exists(exc, name):
    if isinstance(exc, ValueError) and str(exc) == f'Collection {name} already exists':
        return True
    if getattr(exc, 'status_code', None) not in {400, 409}:
        return False
    try:
        body = json.loads(exc.content)
        error = body['status']['error']
        return isinstance(error, str) and re.fullmatch(r'Wrong input: Collection [`\'"]?' + re.escape(name)
                                                     + r'[`\'"]? already exists!?', error) is not None
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _collection(client, name, create=False):
    from qdrant_client import models
    try:
        if create and not client.collection_exists(name):
            try:
                client.create_collection(name, vectors_config=models.VectorParams(
                    size=VECTOR_DIM, distance=models.Distance.DOT, datatype=models.Datatype.FLOAT32))
            except Exception as exc:
                if not _already_exists(exc, name):
                    raise
        info = client.get_collection(name)
    except Exception:
        fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
    config = info.config
    vectors = config.params.vectors
    if (not isinstance(vectors, models.VectorParams) or vectors.size != VECTOR_DIM
            or vectors.distance != models.Distance.DOT
            or vectors.datatype not in (None, models.Datatype.FLOAT32)
            or vectors.multivector_config is not None or vectors.quantization_config is not None
            or config.quantization_config is not None):
        fail('PDF_INDEX_COLLECTION_CONFIG_MISMATCH')


def _records(client, plan, points, require_all):
    expected = {point['id']: point for point in points}
    try:
        records = client.retrieve(plan['collection'], ids=list(expected), with_payload=True,
                                  with_vectors=True, timeout=5)
    except Exception:
        fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
    seen = set()
    for record in records:
        key = str(record.id)
        if key not in expected or key in seen:
            fail('PDF_INDEX_VECTOR_MISMATCH')
        seen.add(key)
        point = expected[key]
        vector = record.vector
        if (not isinstance(vector, list) or len(vector) != VECTOR_DIM
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
            fail('PDF_INDEX_VECTOR_MISMATCH')
        try:
            data = struct.pack('<' + 'f' * VECTOR_DIM, *vector)
        except (OverflowError, struct.error):
            fail('PDF_INDEX_VECTOR_MISMATCH')
        if data != _vector_bytes(point['vector']) or _canonical(record.payload) != _canonical(point['payload']):
            fail('PDF_INDEX_VECTOR_MISMATCH')
    if require_all and seen != set(expected):
        fail('PDF_INDEX_VECTOR_MISSING')
    return seen


@_stable
def verify_plan(plan, client=None):
    _checked_plan(plan)
    with _client(client) as connection:
        _collection(connection, plan['collection'])
        for start in range(0, len(plan['points']), BATCH_SIZE):
            _records(connection, plan, plan['points'][start:start + BATCH_SIZE], True)


@_stable
def verify_selection(plans, client=None):
    """Capture the entire bounded selected ID set in one Qdrant retrieval.

    Selection reads use no ANN search and include no unselected/orphan points.
    A single capture avoids one document changing while other documents are
    being read in separate requests. Cross-store reads are still not atomic.
    """
    if not isinstance(plans, list) or not 1 <= len(plans) <= 4:
        fail('PDF_SEARCH_SELECTION_INVALID', 422)
    for plan in plans:
        _checked_plan(plan)
    if len({plan['publication_id'] for plan in plans}) != len(plans):
        fail('PDF_SEARCH_SELECTION_INVALID', 422)
    first = plans[0]
    if any(plan['profile'] != first['profile'] or plan['collection'] != first['collection'] for plan in plans):
        fail('PDF_SEARCH_PROFILE_MISMATCH')
    points = [point for plan in plans for point in plan['points']]
    if len(points) > MAX_CHUNKS:
        fail('PDF_SEARCH_SELECTION_TOO_LARGE', 422)
    if len({point['id'] for point in points}) != len(points):
        fail('PDF_SEARCH_SELECTION_INVALID', 422)
    with _client(client) as connection:
        _collection(connection, first['collection'])
        _records(connection, first, points, True)


@_stable
def repair_plan(plan, client=None):
    """Re-write points the frozen plan defines but the store no longer holds.

    The plan is the only source of content: nothing is invented, and the plan, the
    publication row and the current head are never modified. Returns bounded counts so
    the caller can record what was missing before the repair.
    """
    _checked_plan(plan)
    from qdrant_client import models
    missing_total = 0
    with _client(client) as connection:
        _collection(connection, plan['collection'], create=True)
        for start in range(0, len(plan['points']), BATCH_SIZE):
            points = plan['points'][start:start + BATCH_SIZE]
            present = _records(connection, plan, points, False)
            missing = [models.PointStruct(**point) for point in points if point['id'] not in present]
            if missing:
                missing_total += len(missing)
                try:
                    connection.upsert(plan['collection'], missing, wait=True)
                except Exception:
                    fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
            _records(connection, plan, points, True)
    return {'collection': plan['collection'], 'expected': len(plan['points']),
            'missing_before': missing_total, 'repaired': missing_total}


@_stable
def write_and_verify(plan, client=None):
    repair_plan(plan, client=client)
