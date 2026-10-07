"""Unified resource budget and hard process deadline for parsing/registration work.

Each unit of parsing, OCR or registration can run in its own process group under a
wall-clock deadline and a bounded output budget, while a PostgreSQL session advisory lock
bounds how many such units run at once across API and worker processes. Exceeding the
deadline terminates the whole group (SIGTERM, then SIGKILL) and reports a stable error
code instead of an exception trace.

This module only provides the primitive. Existing in-process paths stay intact until a
caller opts in by passing a deadline or using `run_bounded`.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from fastapi import HTTPException
import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DEADLINE_SECONDS = 180.0
DEFAULT_GRACE_SECONDS = 5.0
DEFAULT_OUTPUT_BYTES = 256 * 1024
DEFAULT_SLOTS = 2
MIN_DEADLINE_SECONDS = 0.5
MAX_DEADLINE_SECONDS = 3600.0
MIN_OUTPUT_BYTES = 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
DEADLINE_ERROR_CODE = 'TASK_DEADLINE_EXCEEDED'


def fail(code, status=409):
    raise HTTPException(status, detail={'error_code': code})


def _bounded_number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'TASK_BUDGET_{name.upper()}_INVALID')
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f'TASK_BUDGET_{name.upper()}_INVALID')
    return number


def validate_deadline(value):
    """Validate an explicit deadline so callers can fail before spawning work."""
    return _bounded_number(value, 'deadline', MIN_DEADLINE_SECONDS, MAX_DEADLINE_SECONDS)


def _env_number(name, default, minimum, maximum):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return _bounded_number(float(raw.strip()), name.lower(), minimum, maximum)
    except ValueError:
        raise ValueError(f'TASK_BUDGET_{name.upper()}_INVALID')


def configured_deadline(default=DEFAULT_DEADLINE_SECONDS):
    return _env_number('DATAMIND_TASK_DEADLINE_SECONDS', default, MIN_DEADLINE_SECONDS, MAX_DEADLINE_SECONDS)


def configured_grace(default=DEFAULT_GRACE_SECONDS):
    return _env_number('DATAMIND_TASK_GRACE_SECONDS', default, 0.0, 120.0)


def configured_output_limit(default=DEFAULT_OUTPUT_BYTES):
    return _env_number('DATAMIND_TASK_OUTPUT_BYTES', default, MIN_OUTPUT_BYTES, MAX_OUTPUT_BYTES)


def configured_slots(default=DEFAULT_SLOTS):
    raw = os.environ.get('DATAMIND_TASK_SLOTS')
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError('TASK_BUDGET_SLOTS_INVALID')
    if isinstance(value, bool) or not 1 <= value <= 64:
        raise ValueError('TASK_BUDGET_SLOTS_INVALID')
    return value


def effective_dsn(dsn):
    """Resolve the DSN of the database this process actually connected to.

    The isolated regression redirects metadata connections inside the current process,
    so a child process must be handed a DSN that already names the same database instead
    of the original one.
    """
    connection = psycopg2.connect(dsn)
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT current_database()')
            database = cur.fetchone()[0]
    finally:
        connection.close()
    options = parse_dsn(dsn)
    options['dbname'] = database
    return make_dsn(**options)


@contextmanager
def resource_slot(dsn, name, concurrency=None):
    """Hold one of `concurrency` cross-process slots for `name`, or fail immediately."""
    concurrency = concurrency if concurrency is not None else configured_slots()
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 64:
        raise ValueError('TASK_BUDGET_SLOTS_INVALID')
    connection = None
    try:
        connection = psycopg2.connect(dsn, connect_timeout=5)
        connection.autocommit = True
        acquired = False
        with connection.cursor() as cur:
            cur.execute("SET statement_timeout='5s'")
            for slot in range(concurrency):
                cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (f'{name}:{slot}',))
                if cur.fetchone()[0]:
                    acquired = True
                    break
        if not acquired:
            fail('TASK_BUDGET_BUSY', 503)
        yield
    except psycopg2.Error:
        fail('TASK_BUDGET_DATABASE_UNAVAILABLE', 503)
    finally:
        # Closing the session releases its lock even after an exception.
        if connection is not None:
            connection.close()


_RUNNER = r'''
import json, sys
request = json.loads(sys.stdin.read())
try:
    module = __import__(request["module"], fromlist=["_"])
    function = getattr(module, request["function"])
    value = function(*request["args"])
    payload = {"ok": True, "value": value}
except Exception as error:
    detail = getattr(error, "detail", None)
    code = detail.get("error_code") if isinstance(detail, dict) else getattr(error, "code", None)
    if not isinstance(code, str) or not code or len(code) > 96 or not code.replace("_", "").isalnum():
        code = request.get("fallback_error_code") or "TASK_EXECUTION_FAILED"
    payload = {"ok": False, "error_code": code, "status": getattr(error, "status_code", None)}
try:
    # Match the in-process jsonable_encoder path so stored results are byte-identical.
    from fastapi.encoders import jsonable_encoder
    payload = jsonable_encoder(payload)
except Exception:
    pass
sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
'''


class _Capped:
    """Collect at most `limit` bytes while still counting everything read."""

    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()
        self.total = 0

    def feed(self, chunk):
        self.total += len(chunk)
        room = self.limit - len(self.data)
        if room > 0:
            self.data.extend(chunk[:room])

    def truncated(self):
        return self.total > len(self.data)

    def text(self):
        return self.data.decode('utf-8', 'replace')


def _pump(stream, sink):
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            sink.feed(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _terminate_group(process, grace):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=max(1.0, grace))
    except subprocess.TimeoutExpired:
        pass


def run_bounded(module, function, args, *, deadline_s=None, output_limit=None, grace_s=None,
                env=None, fallback_error_code='TASK_EXECUTION_FAILED', cwd=None):
    """Run `module.function(*args)` in a separate process group under a hard deadline.

    `args` must be JSON-serializable; pass `effective_dsn(dsn)` when the target needs a
    database connection. Output is captured up to `output_limit` bytes per stream and
    never raises for a crash or a timeout: the outcome is returned as a status dict.
    """
    deadline = validate_deadline(deadline_s if deadline_s is not None else configured_deadline())
    limit = int(_bounded_number(output_limit if output_limit is not None else configured_output_limit(),
                                'output', MIN_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    grace = _bounded_number(grace_s if grace_s is not None else configured_grace(), 'grace', 0.0, 120.0)
    request = {'module': module, 'function': function, 'args': list(args),
               'fallback_error_code': fallback_error_code}
    child_env = dict(os.environ if env is None else env)
    child_env['PYTHONPATH'] = str(ROOT) + os.pathsep + child_env.get('PYTHONPATH', '')
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, '-c', _RUNNER], cwd=str(cwd or ROOT), env=child_env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    out, err = _Capped(limit), _Capped(limit)
    readers = [threading.Thread(target=_pump, args=(process.stdout, out), daemon=True),
               threading.Thread(target=_pump, args=(process.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(json.dumps(request, ensure_ascii=False).encode('utf-8'))
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
    timed_out = False
    try:
        process.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(process, grace)
    for reader in readers:
        reader.join(timeout=grace + 1)
    base = {'exit_code': process.returncode,
            'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
            'deadline_seconds': deadline, 'stdout_bytes': out.total, 'stderr_bytes': err.total,
            'output_truncated': out.truncated() or err.truncated()}
    if timed_out:
        return {**base, 'status': 'timeout', 'error_code': DEADLINE_ERROR_CODE,
                'stdout': out.text(), 'stderr': err.text()}
    try:
        payload = json.loads(out.text() or '')
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or 'ok' not in payload:
        return {**base, 'status': 'failed',
                'error_code': fallback_error_code if process.returncode else 'TASK_RESULT_INVALID',
                'stdout': out.text(), 'stderr': err.text()}
    if payload.get('ok'):
        return {**base, 'status': 'ok', 'value': payload.get('value')}
    return {**base, 'status': 'failed', 'error_code': payload.get('error_code') or fallback_error_code,
            'status_code': payload.get('status'), 'stdout': out.text(), 'stderr': err.text()}
