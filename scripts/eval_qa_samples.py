#!/usr/bin/env python3
"""WP9 evaluation: stored-corpus searchability audit + one real HTTP upload round trip.

Two independent paths, because they answer two different questions.

``--stored``  *Is the server honest about "has chunks / can be retrieved" today?*
    Reads the published document list anonymously (``GET /v1/documents``) and each
    document's parse contract (``GET /v1/documents/{id}/contract``), then re-derives the
    retrievability trichotomy from the row fields the API does **not** publish
    (``status``/``needs_review``/``chunks``/``block_count``) with the repository's single
    judgement, ``ingestion.upload_pipeline.searchability`` — imported and called, never
    edited. A disagreement between the API's reported triplet and that function is a
    failure, and so is a document whose contract has locators but no chunks yet claims to
    be searchable.

``--http``     *Does a brand-new file actually walk upload → parse → review → index → ask → cite?*
    Uploads one sample over real HTTP (``POST /v1/uploads``, admin identity + CSRF), runs
    the repository's standalone worker once (``python -m ingestion.upload_worker --once``),
    polls the upload detail, approves it, reads the contract locators, then asks a question
    and records the answer/citations verbatim. The index step is expected to be
    ``not_done``: the upload path hard-codes ``vector_index='deferred'`` and writes no
    ``chunks`` rows, so the honest result is "parse + contract evidence yes, retrieval no"
    — which is exactly what this report must say instead of implying a completed loop.

Authentication is not bypassed. The script asks ``GET /v1/auth/me`` for the provisioned
loopback administrator (``datamind/auth.py`` ``demo_allowed``/``demo_identity``) and sends
the CSRF token it returns on every mutation. If the deployment requires a real login
(``DATAMIND_LOCAL_NO_LOGIN=0``) and no session exists, the path records the block and exits
``3`` instead of inventing credentials.

Nothing is deleted, no demo database is written, and no full re-ingestion is run. The only
writes are the ones the two requested paths make: the staged upload the server itself
creates for ``POST /v1/uploads``, and this script's own report file.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / 'data' / 'qa_samples'
MANIFEST_PATH = SAMPLES_DIR / 'manifest.json'
REPORT_PATH = ROOT / 'reports' / 'qa_samples_v1.json'
DEFAULT_BASE_URL = os.environ.get('DATAMIND_BASE_URL') or 'http://127.0.0.1:8000'
CONTROL_QUESTION = '一般商品退款多久到账？'

sys.path.insert(0, str(ROOT))


def _load_builder():
    """Import ``data/qa_samples/build_samples.py`` without making it a package."""
    spec = importlib.util.spec_from_file_location('wp9_build_samples', SAMPLES_DIR / 'build_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()


def _git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(ROOT), check=True,
                              capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


def _sha256(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


# --------------------------------------------------------------------------------------
# HTTP plumbing (every call is logged so the report can quote the real responses)
# --------------------------------------------------------------------------------------

class Api:
    """A tiny logging HTTP client for the running service."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        import requests
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.calls: list[dict] = []

    def request(self, method: str, path: str, *, json_body=None, data=None, headers=None,
                params=None, timeout=None):
        import requests
        started = time.monotonic()
        try:
            response = self.session.request(
                method, self.base_url + path, json=json_body, data=data,
                headers=headers or {}, params=params, timeout=timeout or self.timeout)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            try:
                payload = response.json()
            except ValueError:
                payload = None
            record = {'method': method, 'path': path, 'status': response.status_code,
                      'elapsed_ms': elapsed_ms, 'error_code': _error_code(payload)}
            self.calls.append(record)
            return response, payload, record
        except requests.RequestException as error:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            record = {'method': method, 'path': path, 'status': None, 'elapsed_ms': elapsed_ms,
                      'transport_error': type(error).__name__}
            self.calls.append(record)
            raise

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)


def _error_code(payload):
    if isinstance(payload, dict):
        detail = payload.get('detail')
        if isinstance(detail, dict):
            return detail.get('error_code')
        if isinstance(detail, str):
            return detail
        if payload.get('error_code'):
            return payload['error_code']
    return None


def _summarize_run(payload: dict) -> dict:
    """The honest, bounded summary of one ``POST /v1/chat`` response."""
    if not isinstance(payload, dict):
        return {'parseable': False}
    rag = payload.get('rag_result') or {}
    citations = rag.get('citations') or []
    retrieval = rag.get('retrieval') or {}
    summary = {
        'run_id': payload.get('run_id'),
        'status': payload.get('status'),
        'error_code': payload.get('error_code'),
        'confidence_level': payload.get('confidence_level'),
        'answer': (payload.get('answer') or '')[:400],
        'turns_used': payload.get('turns_used'),
        'citation_count': len(citations),
        'citations': [_citation_summary(item) for item in citations[:5]],
        'retrieval': {'eligible_chunks': retrieval.get('eligible_chunks'),
                      'collections': retrieval.get('collections'),
                      'warnings': retrieval.get('warnings')},
        'answer_coverage': rag.get('answer_coverage'),
    }
    return summary


def _citation_summary(citation: dict) -> dict:
    """Citation identity *and* the locator it points back into the source with.

    A citation's ``locators`` entries are ``{'block_id', 'locator', 'contract_version'}``
    wrappers (``ingestion.contract_store.read_locators``); the region itself lives under
    ``locator``.
    """
    locators = citation.get('locators') or []
    unwrapped = [item.get('locator') if isinstance(item, dict) and isinstance(item.get('locator'), dict)
                 else item for item in locators]
    kinds = sorted({item.get('locator_kind') for item in unwrapped
                    if isinstance(item, dict) and item.get('locator_kind')})
    return {
        'chunk_id': citation.get('chunk_id'),
        'document_id': citation.get('document_id'),
        'title': citation.get('title'),
        'page_start': citation.get('page_start'),
        'page_end': citation.get('page_end'),
        'locator_kinds': kinds,
        'locators': [_trim_locator(item) for item in unwrapped[:3]],
        'page_url': citation.get('page_url'),
        'source_url': citation.get('source_url'),
        'quote_head': (citation.get('quote') or '')[:120],
    }


# --------------------------------------------------------------------------------------
# --stored
# --------------------------------------------------------------------------------------

READONLY_SQL = '''
    SELECT a.document_id, a.title, a.file_type, a.collection, a.status, a.needs_review,
           a.sha256, a.doc_version, a.origin,
           (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id) AS chunks,
           (SELECT count(*) FROM document_contract_blocks b
             WHERE b.document_id=a.document_id) AS blocks,
           (SELECT count(*) FROM document_block_locators l
             WHERE l.document_id=a.document_id) AS locators
      FROM document_assets a
'''


def _readonly_rows():
    """Read the retrievability inputs the API does not publish, read-only, no writes."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from datamind.agent import _read_dsn
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(readonly=True)
        cur.execute(READONLY_SQL)
        return {row['document_id']: dict(row) for row in cur.fetchall()}


def _contract_summary(api: Api, document_id: str, limit: int = 3) -> dict:
    response, payload, record = api.get(f'/v1/documents/{document_id}/contract',
                                        params={'limit': limit})
    summary = {'http_status': response.status_code, 'error_code': _error_code(payload)}
    if not isinstance(payload, dict):
        return summary
    locators = payload.get('locators') or []
    blocks = payload.get('blocks') or []
    # Two disjoint stores, both carrying locators: page-shaped PDF parses write
    # `document_block_locators` (returned as `locators`, each entry is the block row with the
    # validated locator nested under `locator`), while Office/DXF parses write
    # `document_contract_blocks` (`blocks`) whose `locator` is an `office_object`/`sheet_range`
    # /`cad_entity` payload. Reading only one of them would report "no locator" for a format
    # that has one.
    all_locators = []
    for item in locators:
        if isinstance(item, dict):
            nested = item.get('locator')
            all_locators.append(nested if isinstance(nested, dict) else item)
    for item in blocks:
        if isinstance(item, dict) and isinstance(item.get('locator'), dict):
            all_locators.append(item['locator'])
    summary.update({
        'block_count': payload.get('block_count'),
        'locator_count': payload.get('locator_count'),
        'locator_total': payload.get('locator_total'),
        'relation_count': payload.get('relation_count'),
        'counts': payload.get('counts'),
        'totals': payload.get('totals'),
        'parse_contract': payload.get('parse_contract'),
        'unsupported_features': payload.get('unsupported_features'),
        'sampled_locator_kinds': sorted({item.get('locator_kind') for item in all_locators
                                         if item.get('locator_kind')}),
        'sampled_block_types': sorted({item.get('block_type') for item in blocks
                                       if isinstance(item, dict)}),
        'locator_fields_present': sorted(
            set().union(*[set(item) for item in all_locators]) if all_locators else set()),
        'sample_locator': _trim_locator(all_locators[0]) if all_locators else None,
        'contract_block_locators': len(all_locators) - len(locators),
    })
    return summary


def _trim_locator(locator):
    """Keep the fields a reader needs to judge "this points back to the source"."""
    if not isinstance(locator, dict):
        return locator
    keep = ('locator_kind', 'page_no', 'bbox', 'page_width', 'page_height', 'unit',
            'coordinate_system', 'part', 'paragraph', 'table', 'slide', 'shape', 'sheet',
            'range', 'layout', 'layer', 'model_xy')
    return {key: locator.get(key) for key in keep if key in locator}


def stored_path(api: Api) -> dict:
    from ingestion import upload_pipeline
    checked, problems = BUILDER.check_manifest()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    result: dict = {
        'mode': 'stored',
        'manifest': {'path': str(MANIFEST_PATH.relative_to(ROOT)), 'frozen': checked,
                     'problems': problems, 'digest': manifest.get('digest'),
                     'samples': manifest['counts']['samples'],
                     'formats': manifest['counts']['formats'],
                     'questions': manifest['counts']['questions']},
        'anonymous_reads': [],
        'documents': [],
        'samples': [],
        'consistency': {},
        'upload_pipeline_semantics': {
            'function': 'ingestion.upload_pipeline.searchability',
            'vector_index': upload_pipeline.VECTOR_INDEX,
            'vector_index_note': upload_pipeline.VECTOR_INDEX_NOTE,
            'index_states': [upload_pipeline.INDEX_STATE_INDEXED,
                             upload_pipeline.INDEX_STATE_NO_CHUNKS,
                             upload_pipeline.INDEX_STATE_NOT_APPROVED],
        },
    }
    # What identity do these "anonymous" reads actually run as? On this host every loopback
    # request is the provisioned demo administrator (`datamind/auth.py` `demo_allowed`), so
    # "anonymous" here means "no session cookie was sent" — the report says which identity the
    # server resolved instead of claiming more anonymity than the deployment grants.
    try:
        _, me, me_record = api.get('/v1/auth/me')
        result['anonymous_reads'].append(me_record)
        user = (me or {}).get('user') or {}
        result['read_identity'] = {'user_id': user.get('user_id'), 'username': user.get('username'),
                                   'roles': user.get('roles'),
                                   'session_cookie_sent': False}
    except Exception as error:  # the read path itself does not depend on this probe
        result['read_identity'] = {'error': type(error).__name__}
    result['read_identity_note'] = (
        '本机 loopback 请求由 datamind/auth.py 的 demo_allowed 视为已配置的 demo 管理员；'
        '“匿名”仅指未发送会话 cookie、未使用任何伪造凭证。')
    response, payload, record = api.get('/v1/documents')
    result['anonymous_reads'].append(record)
    if response.status_code != 200 or not isinstance(payload, dict):
        result['failures'] = [{'kind': 'documents_list_unavailable',
                               'http_status': response.status_code,
                               'error_code': _error_code(payload)}]
        return result
    documents = payload.get('documents') or []
    rows = _readonly_rows()
    consistent = inconsistent = 0
    index_states: dict[str, int] = {}
    for item in documents:
        document_id = item.get('document_id')
        row = rows.get(document_id)
        triple = (bool(item.get('searchable')), item.get('index_state'), item.get('index_note'))
        index_states[item.get('index_state')] = index_states.get(item.get('index_state'), 0) + 1
        entry = {'document_id': document_id, 'title': item.get('title'),
                 'collection': item.get('collection'), 'file_type': item.get('file_type'),
                 'sha256': item.get('sha256'), 'doc_version': item.get('doc_version'),
                 'api': {'searchable': item.get('searchable'), 'index_state': item.get('index_state'),
                         'index_note': item.get('index_note')}}
        if row is None:
            entry['in_db_row'] = False
            inconsistent += 1
            entry['consistency'] = 'row_missing'
        else:
            expected = upload_pipeline.searchability(
                row['status'], row['needs_review'], row['chunks'], block_count=row['blocks'])
            entry['in_db_row'] = True
            entry['db'] = {'status': row['status'], 'needs_review': row['needs_review'],
                           'chunks': row['chunks'], 'contract_blocks': row['blocks'],
                           'locators': row['locators']}
            entry['expected_triplet'] = list(expected)
            entry['consistency'] = 'consistent' if tuple(expected) == triple else 'mismatch'
            if tuple(expected) == triple:
                consistent += 1
            else:
                inconsistent += 1
            contract = _contract_summary(api, document_id)
            result['anonymous_reads'].append({'method': 'GET',
                                              'path': f'/v1/documents/{document_id}/contract',
                                              'status': contract['http_status'],
                                              'error_code': contract.get('error_code')})
            entry['contract'] = contract
            # The contract's two counts are the two disjoint stores: `block_count` is
            # `document_contract_blocks`, `locator_count` is the page-shaped
            # `document_block_locators`. Compare each against its own read-only count.
            notes = []
            if contract.get('block_count') not in (None, row['blocks']):
                notes.append(f"contract block_count={contract.get('block_count')} vs db={row['blocks']}")
            if contract.get('locator_count') not in (None, row['locators']):
                notes.append(f"contract locator_count={contract.get('locator_count')} vs db={row['locators']}")
            if notes:
                entry['contract_count_note'] = '; '.join(notes)
            if item.get('searchable') and (row['chunks'] or 0) <= 0:
                # Cannot be reached through the real routes (searchable is derived from the
                # same function), so this would mean the API and the function disagree.
                entry['consistency'] = 'searchable_without_chunks'
                inconsistent += 1
        result['documents'].append(entry)

    # Match the frozen samples to what is actually stored, by content hash (not by title).
    # Two views are kept apart on purpose: `in_store` is "a `document_assets` row with this
    # exact SHA-256 exists" (read-only DB), while `published_visible` is "the anonymous
    # `GET /v1/documents` list returns it" — an approved upload with no chunks is in the
    # first and absent from the second, which is the honesty property under test.
    by_sha = {item.get('sha256'): item for item in documents}
    row_by_sha = {row['sha256']: row for row in rows.values()}
    in_store = published = 0
    assertion_ok = assertion_failed = 0
    for entry in manifest['samples']:
        path = SAMPLES_DIR / entry['file']
        stored_item = by_sha.get(entry['sha256'])
        row = row_by_sha.get(entry['sha256'])
        sample = {'file': entry['file'], 'format': entry['format'], 'sha256': entry['sha256'],
                  'in_store': row is not None,
                  'published_visible': stored_item is not None,
                  'document_id': (row or {}).get('document_id'),
                  'expected_locator_kinds': entry['expected_locator_kinds'],
                  'stored': None,
                  'status': 'not_in_store',
                  'questions': []}
        if row is not None:
            in_store += 1
            published += 1 if stored_item is not None else 0
            derived = upload_pipeline.searchability(row['status'], row['needs_review'], row['chunks'],
                                                    block_count=row['blocks'])
            sample['status'] = ('in_store_searchable' if derived[0] else 'in_store_unretrievable')
            sample['stored'] = {
                'source': ('anonymous_documents_list' if stored_item is not None
                           else 'readonly_db_derivation'),
                'status': row['status'], 'needs_review': row['needs_review'],
                'searchable': stored_item.get('searchable') if stored_item else derived[0],
                'index_state': stored_item.get('index_state') if stored_item else derived[1],
                'index_note': stored_item.get('index_note') if stored_item else derived[2],
                'chunks': row['chunks'], 'contract_blocks': row['blocks'],
                'locators': row['locators']}
            contract = _contract_summary(api, row['document_id'], limit=10)
            sample['contract_http_status'] = contract.get('http_status')
            observed = contract.get('sampled_locator_kinds') or []
            sample['observed_locator_kinds'] = observed
            if contract.get('http_status') == 200:
                sample['locator_kinds_ok'] = (set(observed) == set(entry['expected_locator_kinds'])
                                              if observed else None)
            else:
                # An approved upload whose asset was never published as `indexed` is not
                # anonymously readable (R05 gate). --http reads it with the provisioned admin
                # identity instead; the locator expectation is checked there.
                sample['locator_kinds_ok'] = None
                sample['anonymous_contract_note'] = (
                    'document is in the store but not published as indexed; the anonymous '
                    'contract read returns ' + str(contract.get('http_status')))
        for question in entry['questions']:
            check = BUILDER.check_assertion(path, entry, question)
            if check.get('ok') and check.get('operand_sources_ok', True):
                assertion_ok += 1
            else:
                assertion_failed += 1
            sample['questions'].append({
                'id': question['id'], 'question': question['question'],
                'answer': question['answer'], 'checkable_from': question['checkable_from'],
                'assertion': check,
                # The QA verdict cannot be produced without an index: the retriever reads
                # `chunks` only, and the upload path writes none.
                'qa_verdict': 'not_attempted',
                'qa_reason': 'no_chunks_in_store_vector_index_deferred',
            })
        result['samples'].append(sample)

    result['consistency'] = {
        'documents_reported_by_api': len(documents),
        'documents_with_db_row': sum(1 for entry in result['documents'] if entry.get('in_db_row')),
        'consistent': consistent,
        'inconsistent': inconsistent,
        'index_state_counts': index_states,
        'searchable_true': sum(1 for entry in documents if entry.get('searchable')),
        'searchable_false': sum(1 for entry in documents if not entry.get('searchable')),
        'db_document_rows': len(rows),
        'db_rows_not_published': sum(1 for row in rows.values()
                                     if row['status'] != 'indexed' or row['needs_review']),
        'samples_in_store': in_store,
        'samples_published_visible': published,
        'samples_total': len(manifest['samples']),
        'assertions_ok': assertion_ok,
        'assertions_failed': assertion_failed,
        'manifest_frozen': checked,
    }
    result['failures'] = []
    if not checked:
        result['failures'].append({'kind': 'manifest_not_frozen', 'problems': problems})
    for entry in result['documents']:
        if entry.get('consistency') != 'consistent':
            result['failures'].append({'kind': 'searchability_inconsistent',
                                       'document_id': entry['document_id'],
                                       'api': entry.get('api'), 'db': entry.get('db'),
                                       'detail': entry.get('consistency')})
    for sample in result['samples']:
        if sample.get('locator_kinds_ok') is False:
            result['failures'].append({'kind': 'locator_kinds_mismatch', 'file': sample['file'],
                                       'expected': sample['expected_locator_kinds'],
                                       'observed': sample.get('observed_locator_kinds')})
    return result


# --------------------------------------------------------------------------------------
# --http
# --------------------------------------------------------------------------------------

class Blocked(RuntimeError):
    """A dependency the path needs is genuinely unavailable (recorded, not worked around)."""


def _identity(api: Api) -> dict:
    """Provisioned loopback identity + CSRF token; never a fabricated session."""
    response, payload, record = api.get('/v1/auth/me')
    if response.status_code != 200 or not isinstance(payload, dict) or not payload.get('user'):
        raise Blocked(json.dumps({'step': 'auth', 'http_status': response.status_code,
                                  'error_code': _error_code(payload),
                                  'note': 'no loopback demo identity and no session credential'},
                                 ensure_ascii=False))
    return {'user': payload['user'], 'csrf_token': payload.get('csrf_token'), 'call': record}


def _worker_once(api: Api, timeout: float) -> dict:
    """Run the repository's standalone parse worker once and record its exit code."""
    command = [sys.executable, '-m', 'ingestion.upload_worker', '--once']
    try:
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                                   timeout=timeout)
        return {'command': ' '.join(command), 'returncode': completed.returncode,
                'stdout_tail': (completed.stdout or '').strip().splitlines()[-6:],
                'stderr_tail': (completed.stderr or '').strip().splitlines()[-4:]}
    except subprocess.TimeoutExpired as error:
        return {'command': ' '.join(command), 'returncode': None,
                'timeout_seconds': timeout,
                'stdout_tail': (error.stdout or b'').decode('utf-8', 'replace').strip().splitlines()[-4:]}


def _upload_detail_summary(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {'parseable': False}
    return {key: payload.get(key) for key in
            ('upload_id', 'job_id', 'document_id', 'filename', 'status', 'review_state',
             'review_required', 'searchable', 'index_state', 'index_note', 'block_count',
             'locator_count', 'vector_index', 'vector_index_note', 'error_code', 'attempt',
             'page_count', 'parser_version', 'source_sha256', 'contract_url',
             'unsupported_features', 'analysis')}


def http_path(api: Api, sample_file: str, *, control: bool, worker_timeout: float) -> dict:
    from ingestion import upload_pipeline
    result: dict = {'mode': 'http', 'steps': [], 'blocked': None}
    path = SAMPLES_DIR / sample_file
    if not path.is_file():
        raise SystemExit('sample missing: %s' % path)

    identity = _identity(api)
    result['auth'] = {'user_id': (identity['user'] or {}).get('user_id'),
                      'username': (identity['user'] or {}).get('username'),
                      'roles': (identity['user'] or {}).get('roles'),
                      'csrf_token_present': bool(identity['csrf_token']),
                      'call': identity['call']}
    result['steps'].append({'step': 'auth', 'http': identity['call'],
                            'summary': {k: v for k, v in result['auth'].items() if k != 'call'}})

    headers = {'X-CSRF-Token': identity['csrf_token'] or ''}
    body = path.read_bytes()
    response, payload, record = api.post(
        '/v1/uploads', params={'filename': path.name, 'provenance': 'synthetic_demo'},
        data=body, headers={**headers, 'Content-Type': 'application/octet-stream'})
    result['steps'].append({'step': 'upload', 'http': record, 'summary': payload,
                            'request_bytes': len(body),
                            'request_sha256': _sha256(body)})
    if response.status_code != 201 or not isinstance(payload, dict):
        result['blocked'] = {'step': 'upload', 'http_status': response.status_code,
                             'error_code': _error_code(payload)}
        return result
    upload_id = payload['upload_id']

    worker = _worker_once(api, worker_timeout)
    result['steps'].append({'step': 'worker', 'summary': worker})

    detail = None
    for _ in range(12):
        _, detail, record = api.get(f'/v1/uploads/{upload_id}')
        status = (detail or {}).get('status')
        result['steps'].append({'step': 'poll', 'http': record,
                                'summary': {'status': status,
                                            'searchable': (detail or {}).get('searchable'),
                                            'index_state': (detail or {}).get('index_state'),
                                            'worker': (detail or {}).get('worker')}})
        if status in upload_pipeline.TERMINAL:
            break
        time.sleep(2.0)
    result['before_approval'] = _upload_detail_summary(detail or {})
    if (detail or {}).get('status') not in upload_pipeline.TERMINAL:
        result['blocked'] = {'step': 'parse', 'job_status': (detail or {}).get('status'),
                             'note': 'worker did not reach a terminal state'}
        return result
    document_id = detail['document_id']
    doc_version = _document_version(document_id)

    response, approval, record = api.post(f'/v1/uploads/{upload_id}/approve', headers=headers)
    result['steps'].append({'step': 'approve', 'http': record,
                            'summary': _upload_detail_summary(approval or {})})
    _, after, record = api.get(f'/v1/uploads/{upload_id}')
    result['steps'].append({'step': 'detail_after_approval', 'http': record,
                            'summary': _upload_detail_summary(after or {})})
    result['after_approval'] = _upload_detail_summary(after or {})

    contract = _contract_summary(api, document_id, limit=10)
    result['steps'].append({'step': 'contract',
                            'http': {'method': 'GET',
                                     'path': f'/v1/documents/{document_id}/contract',
                                     'status': contract.get('http_status')},
                            'summary': {key: contract.get(key) for key in
                                        ('block_count', 'locator_count', 'locator_total',
                                         'sampled_locator_kinds', 'sampled_block_types',
                                         'parse_contract', 'unsupported_features',
                                         'sample_locator')}})
    result['contract'] = contract

    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    entry = next(item for item in manifest['samples'] if item['file'] == sample_file)
    question = entry['questions'][0]
    observed_kinds = contract.get('sampled_locator_kinds') or []
    result['locator_kinds_check'] = {
        'expected': entry['expected_locator_kinds'],
        'observed': observed_kinds,
        'ok': set(observed_kinds) == set(entry['expected_locator_kinds']) if observed_kinds else None,
        'expected_locator_fields': entry['expected_locator_fields'],
        'locator_fields_present': contract.get('locator_fields_present'),
        'sample_locator': contract.get('sample_locator'),
    }

    # (1) Ask about the new file with an explicit file scope.
    reference = {'document_id': document_id, 'document_version': doc_version,
                 'source_sha256': detail['source_sha256'],
                 'parser_version': detail.get('parser_version') or 'unknown'}
    response, payload, record = api.post('/v1/chat', headers=headers, json_body={
        'question': question['question'], 'mode': 'rag',
        'scope': {'document_refs': [reference]}}, timeout=180.0)
    result['steps'].append({'step': 'ask_with_document_ref', 'http': record,
                            'request_reference': reference,
                            'summary': _summarize_run(payload or {})})
    result['ask_with_document_ref'] = {'request_reference': reference,
                                       'response': _summarize_run(payload or {})}

    # (2) Ask the same question with no explicit file scope (the default document route).
    response, payload, record = api.post('/v1/chat', headers=headers, json_body={
        'question': question['question'], 'mode': 'rag'}, timeout=180.0)
    result['steps'].append({'step': 'ask_default_scope', 'http': record,
                            'summary': _summarize_run(payload or {})})
    result['ask_default_scope'] = _summarize_run(payload or {})

    # (3) Control: the same route on already-indexed knowledge-base content.
    if control:
        response, payload, record = api.post('/v1/chat', headers=headers, json_body={
            'question': CONTROL_QUESTION, 'mode': 'rag'}, timeout=180.0)
        result['steps'].append({'step': 'control_ask_indexed_kb', 'http': record,
                                'summary': _summarize_run(payload or {})})
        result['control_ask'] = _summarize_run(payload or {})

    indexed = bool((after or {}).get('searchable'))
    result['index_wiring'] = {
        'vector_index': (after or {}).get('vector_index') or upload_pipeline.VECTOR_INDEX,
        'vector_index_note': (after or {}).get('vector_index_note'),
        'index_state': (after or {}).get('index_state'),
        'automatic_index': 'done' if indexed else 'not_done',
        'reason': ('上传路径只写元数据与契约证据，不写 chunks/Qdrant 行；检索器 '
                   'datamind.knowledge 只读 chunks，因此新文件不可检索。'),
    }
    return result


def _document_version(document_id: str):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from datamind.agent import _read_dsn
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(readonly=True)
        cur.execute('SELECT doc_version FROM document_assets WHERE document_id=%s', (document_id,))
        row = cur.fetchone()
    return (row or {}).get('doc_version') or 1


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------

def _api_version(api: Api) -> str | None:
    try:
        response, payload, _ = api.get('/openapi.json')
    except Exception:
        return None
    return ((payload or {}).get('info') or {}).get('version') if response.status_code == 200 else None


def _load_report(path: Path) -> dict:
    """Start from the report already on disk so a single-path run keeps the other section.

    The merge happens **in memory** and the file is written once at the end. An earlier
    version re-read the file for each section, which silently threw away the section produced
    by the current run and kept the previous run's copy instead (a stale manifest digest and
    a stale per-sample shape were the visible symptom).
    """
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(existing, dict):
                return existing
        except ValueError:
            pass
    return {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--stored', action='store_true', help='audit the already-stored corpus')
    parser.add_argument('--http', action='store_true', help='upload one new file over real HTTP')
    parser.add_argument('--all', action='store_true', help='run both paths')
    parser.add_argument('--file', default='05_service_sla_manual.docx',
                        help='sample to upload in --http (default: the DOCX SLA sample)')
    parser.add_argument('--control', action='store_true', default=True,
                        help='also ask one question against already-indexed knowledge base content')
    parser.add_argument('--no-control', dest='control', action='store_false')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--worker-timeout', type=float, default=180.0)
    parser.add_argument('--report', default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    if not (args.stored or args.http or args.all):
        parser.error('choose --stored, --http or --all')

    api = Api(args.base_url)
    report_path = Path(args.report)
    run_stored = args.stored or args.all
    run_http = args.http or args.all
    exit_code = 0

    report = _load_report(report_path)
    report.update({'schema': 'qa_samples_report_v1',
                   'generated_at': datetime.now(timezone.utc).isoformat(),
                   'commit': _git_commit(), 'api_version': _api_version(api),
                   'base_url': api.base_url})

    if run_stored:
        stored = stored_path(api)
        report['stored'] = stored
        if stored.get('failures'):
            exit_code = max(exit_code, 1)
        print(json.dumps({'stored': stored['consistency'],
                          'failures': len(stored.get('failures') or [])}, ensure_ascii=False))

    if run_http:
        try:
            http_result = http_path(api, args.file, control=args.control,
                                    worker_timeout=args.worker_timeout)
        except Blocked as error:
            http_result = {'mode': 'http', 'blocked': json.loads(str(error)), 'steps': []}
            exit_code = max(exit_code, 3)
        report['http'] = http_result
        section = report.get('http') or {}
        if section.get('blocked'):
            exit_code = max(exit_code, 3)
        print(json.dumps({'http': {'blocked': section.get('blocked'),
                                   'index_wiring': section.get('index_wiring')}},
                         ensure_ascii=False))

    # `index_wiring` and `not_done` are derived from the sections actually present in this
    # report rather than carried over from an earlier run: a stale "not_done" list is exactly
    # the drift this work package exists to prevent.
    stored_section = report.get('stored')
    http_section = report.get('http')
    wiring = (http_section or {}).get('index_wiring') or {}
    report['index_wiring'] = wiring or {
        'vector_index': 'deferred',
        'automatic_index': 'not_done',
        'reason': ('本轮只跑了 --stored；上传路径 vector_index=deferred，未接入自动切片/索引。')}
    not_done = []
    if stored_section:
        consistency = stored_section.get('consistency') or {}
        not_done.append('自动切片/索引未接入（上传路径 vector_index=deferred）；'
                        '--stored 只判定可检索性，不产生问答答案')
        not_done.append('样本入库 %s/%s：其余样本未入库，其答案断言只做离线可判定性复核（非模型回答）'
                        % (consistency.get('samples_in_store'), consistency.get('samples_total')))
    if http_section is not None and http_section:
        not_done.append('自动切片/索引未接入：vector_index=%s，index_state=%s，'
                        'automatic_index=%s' % (wiring.get('vector_index'), wiring.get('index_state'),
                                                wiring.get('automatic_index')))
        not_done.append('新文件的问答与引用定位未闭环：只到契约 block/locator 级定位，'
                        '未产生可点击到原位的引用')
    if http_section is not None and http_section.get('blocked'):
        not_done.append('HTTP 路径被阻塞：' + json.dumps(http_section['blocked'], ensure_ascii=False))
    report['not_done'] = not_done

    # One machine-readable failure list for the whole report: rule inconsistencies from
    # `--stored`, plus every non-COMPLETED answer the `--http` path produced (classified by
    # status/error_code, never dropped from the count).
    failures = list((stored_section or {}).get('failures') or [])
    if report['index_wiring'].get('automatic_index') != 'done':
        failures.append({'kind': 'automatic_index_not_implemented',
                         'detail': 'upload path hard-codes vector_index=deferred and writes no '
                                   'chunks; datamind.knowledge reads chunks only',
                         'vector_index': report['index_wiring'].get('vector_index'),
                         'index_state': report['index_wiring'].get('index_state')})
    if http_section:
        if http_section.get('blocked'):
            failures.append({'kind': 'http_path_blocked', 'detail': http_section['blocked']})
        check = http_section.get('locator_kinds_check') or {}
        if check.get('ok') is False:
            failures.append({'kind': 'locator_kinds_mismatch', 'detail': check})
        for step in ('ask_with_document_ref', 'ask_default_scope', 'control_ask'):
            answer = http_section.get(step)
            if not answer:
                continue
            # `ask_with_document_ref` keeps its request reference beside the response;
            # the other two steps store the summary directly.
            response = (answer.get('response')
                        if isinstance(answer, dict) and isinstance(answer.get('response'), dict)
                        else answer) or {}
            if response.get('status') != 'COMPLETED':
                failures.append({'kind': 'qa_not_completed', 'step': step,
                                 'status': response.get('status'),
                                 'error_code': response.get('error_code'),
                                 'answer': response.get('answer'),
                                 'citation_count': response.get('citation_count'),
                                 'why': ('the newly uploaded file has no chunks (index not wired)'
                                         if step != 'control_ask' else 'control question failed')})
    report['failures'] = failures
    report['calls'] = api.calls
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('报告已写入：%s' % report_path)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
