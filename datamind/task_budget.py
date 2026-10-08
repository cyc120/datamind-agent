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
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import make_dsn, parse_dsn

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX platform
    resource = None

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
CPU_LIMIT_ERROR_CODE = 'TASK_CPU_LIMIT_EXCEEDED'
RLIMIT_UNSUPPORTED_ERROR_CODE = 'TASK_BUDGET_RLIMIT_UNSUPPORTED'
SPAWN_ERROR_CODE = 'TASK_BUDGET_SPAWN_FAILED'
_RLIMIT_PROBE = {'memory': 'resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))',
                 'cpu': 'resource.setrlimit(resource.RLIMIT_CPU,(1,1))'}
_RLIMIT_SUPPORT = {}
MEMORY_LIMIT_ERROR_CODE = 'TASK_MEMORY_LIMIT_EXCEEDED'
MIN_MEMORY_BYTES = 16 * 1024 * 1024
MAX_MEMORY_BYTES = 64 * 1024 * 1024 * 1024
MIN_CPU_SECONDS = 1
MAX_CPU_SECONDS = 3600
LEASE_TTL_DEFAULT = 60
LEASE_TTL_MAX = 3600
_LEASE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


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
except MemoryError:
    payload = {"ok": False, "error_code": "TASK_MEMORY_LIMIT_EXCEEDED", "status": None}
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


def _child_env(env):
    child_env = dict(os.environ if env is None else env)
    child_env['PYTHONPATH'] = str(ROOT) + os.pathsep + child_env.get('PYTHONPATH', '')
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    return child_env


def _optional_int(value, name, minimum, maximum):
    if value is None:
        return None
    return int(_bounded_number(value, name, minimum, maximum))


def validate_memory_bytes(value):
    """Address-space cap for a child process, or None for no cap."""
    return _optional_int(value, 'memory', MIN_MEMORY_BYTES, MAX_MEMORY_BYTES)


def validate_cpu_seconds(value):
    """CPU-time cap for a child process, or None for no cap."""
    return _optional_int(value, 'cpu', MIN_CPU_SECONDS, MAX_CPU_SECONDS)


def _policy(deadline_s, output_limit, grace_s, poll_interval, memory_bytes=None, cpu_seconds=None):
    deadline = validate_deadline(deadline_s if deadline_s is not None else configured_deadline())
    limit = int(_bounded_number(output_limit if output_limit is not None else configured_output_limit(),
                                'output', MIN_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    grace = _bounded_number(grace_s if grace_s is not None else configured_grace(), 'grace', 0.0, 120.0)
    interval = _bounded_number(poll_interval, 'poll', 0.05, 5.0)
    memory = validate_memory_bytes(memory_bytes)
    cpu = validate_cpu_seconds(cpu_seconds)
    if (memory is not None or cpu is not None) and resource is None:
        raise ValueError('TASK_BUDGET_RLIMIT_UNAVAILABLE')
    return deadline, limit, grace, interval, memory, cpu


def rlimit_supported(kind):
    """Report whether this platform actually enforces the requested rlimit kind.

    Probed once in a throwaway child: macOS refuses ``RLIMIT_AS``/``RLIMIT_DATA`` with
    EINVAL, so a memory cap cannot be honoured there and callers must fail closed.
    """
    if kind not in _RLIMIT_PROBE:
        raise ValueError('TASK_BUDGET_RLIMIT_UNKNOWN')
    if kind in _RLIMIT_SUPPORT:
        return _RLIMIT_SUPPORT[kind]
    if resource is None:
        _RLIMIT_SUPPORT[kind] = False
        return False
    try:
        probe = subprocess.run([sys.executable, '-c', 'import resource;' + _RLIMIT_PROBE[kind]],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        _RLIMIT_SUPPORT[kind] = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _RLIMIT_SUPPORT[kind] = False
    return _RLIMIT_SUPPORT[kind]


def _unsupported_limit(memory_bytes, cpu_seconds):
    if memory_bytes is not None and not rlimit_supported('memory'):
        return RLIMIT_UNSUPPORTED_ERROR_CODE
    if cpu_seconds is not None and not rlimit_supported('cpu'):
        return RLIMIT_UNSUPPORTED_ERROR_CODE
    return None


def _refused(base, code):
    """A limit the platform will not enforce is refused, never silently ignored."""
    return {**base, 'status': 'failed', 'error_code': code, 'stdout': '', 'stderr': ''}


def _rlimit_hook(memory_bytes, cpu_seconds):
    """Apply POSIX address-space / CPU caps in the child before it execs.

    `RLIMIT_AS` bounds virtual address space (not RSS) and `RLIMIT_CPU` bounds CPU time;
    neither is a cgroup, so a runaway child is still only bounded, not containerised.
    """
    if memory_bytes is None and cpu_seconds is None:
        return None

    def apply():
        if memory_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    return apply


def _signal_code(exit_code):
    """Map a signal death caused by a configured limit to a stable error code."""
    if not isinstance(exit_code, int) or exit_code >= 0:
        return None
    signum = -exit_code
    if signum == signal.SIGXCPU:
        return CPU_LIMIT_ERROR_CODE
    if signum in {signal.SIGKILL, signal.SIGSEGV, signal.SIGABRT}:
        return MEMORY_LIMIT_ERROR_CODE
    return None


def _run_child(argv, stdin_text, deadline, limit, grace, interval, child_env, cwd, cancel_event,
               memory_bytes=None, cpu_seconds=None):
    """Start `argv` in its own process group, feed stdin, and enforce the deadline."""
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=str(cwd or ROOT), env=child_env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True,
                                   preexec_fn=_rlimit_hook(memory_bytes, cpu_seconds))
    except (OSError, subprocess.SubprocessError):
        # e.g. the platform refused a limit inside preexec_fn.
        base = {'exit_code': None, 'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
                'deadline_seconds': deadline, 'stdout_bytes': 0, 'stderr_bytes': 0,
                'output_truncated': False,
                'limits': {'memory_bytes': memory_bytes, 'cpu_seconds': cpu_seconds}}
        return base, _Capped(limit), _Capped(limit), False, False, RLIMIT_UNSUPPORTED_ERROR_CODE
    out, err = _Capped(limit), _Capped(limit)
    readers = [threading.Thread(target=_pump, args=(process.stdout, out), daemon=True),
               threading.Thread(target=_pump, args=(process.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        if stdin_text:
            process.stdin.write(stdin_text.encode('utf-8'))
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
    timed_out = cancelled = False
    try:
        if cancel_event is None:
            process.wait(timeout=deadline)
        else:
            end = time.monotonic() + deadline
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, deadline)
                if cancel_event.wait(min(interval, remaining)):
                    cancelled = True
                    _terminate_group(process, grace)
                    break
                if process.poll() is not None:
                    break
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(process, grace)
    for reader in readers:
        reader.join(timeout=grace + 1)
    exit_code = process.returncode
    base = {'exit_code': exit_code,
            'elapsed_ms': round((time.monotonic() - started) * 1000, 1),
            'deadline_seconds': deadline, 'stdout_bytes': out.total, 'stderr_bytes': err.total,
            'output_truncated': out.truncated() or err.truncated(),
            'limits': {'memory_bytes': memory_bytes, 'cpu_seconds': cpu_seconds}}
    return base, out, err, timed_out, cancelled, None


def run_bounded_command(argv, stdin_text='', *, deadline_s=None, output_limit=None, grace_s=None,
                        env=None, cwd=None, cancel_event=None, poll_interval=0.5,
                        fallback_error_code='TASK_EXECUTION_FAILED', memory_bytes=None,
                        cpu_seconds=None):
    """Run an explicit command in its own process group under the same budget.

    Use this for targets that need their own interpreter or entry point (for example the
    isolated formula environment). Success only means the process exited 0; interpreting
    stdout is the caller's responsibility. Timeouts and cancellations terminate the whole
    group and are reported with the shared codes.
    """
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(x, str) and x for x in argv):
        raise ValueError('TASK_BUDGET_COMMAND_INVALID')
    deadline, limit, grace, interval, memory, cpu = _policy(deadline_s, output_limit, grace_s,
                                                           poll_interval, memory_bytes, cpu_seconds)
    unsupported = _unsupported_limit(memory, cpu)
    if unsupported:
        return _refused({'exit_code': None, 'elapsed_ms': 0.0, 'deadline_seconds': deadline,
                         'stdout_bytes': 0, 'stderr_bytes': 0, 'output_truncated': False,
                         'limits': {'memory_bytes': memory, 'cpu_seconds': cpu}}, unsupported)
    base, out, err, timed_out, cancelled, spawn_error = _run_child(
        list(argv), stdin_text, deadline, limit, grace, interval, _child_env(env), cwd, cancel_event,
        memory, cpu)
    if spawn_error:
        return _refused(base, spawn_error)
    if cancelled:
        return {**base, 'status': 'cancelled', 'error_code': 'TASK_CANCELLED',
                'stdout': out.text(), 'stderr': err.text()}
    if timed_out:
        return {**base, 'status': 'timeout', 'error_code': DEADLINE_ERROR_CODE,
                'stdout': out.text(), 'stderr': err.text()}
    if base['exit_code'] == 0:
        return {**base, 'status': 'ok', 'stdout': out.text(), 'stderr': err.text()}
    return {**base, 'status': 'failed', 'error_code': _signal_code(base['exit_code']) or fallback_error_code,
            'stdout': out.text(), 'stderr': err.text()}


def run_bounded(module, function, args, *, deadline_s=None, output_limit=None, grace_s=None,
                env=None, fallback_error_code='TASK_EXECUTION_FAILED', cwd=None,
                cancel_event=None, poll_interval=0.5, memory_bytes=None, cpu_seconds=None):
    """Run `module.function(*args)` in a separate process group under a hard deadline.

    `args` must be JSON-serializable; pass `effective_dsn(dsn)` when the target needs a
    database connection. Output is captured up to `output_limit` bytes per stream and
    never raises for a crash or a timeout: the outcome is returned as a status dict.
    Setting `cancel_event` terminates the group early and returns `status='cancelled'`.
    """
    deadline, limit, grace, interval, memory, cpu = _policy(deadline_s, output_limit, grace_s,
                                                           poll_interval, memory_bytes, cpu_seconds)
    request = {'module': module, 'function': function, 'args': list(args),
               'fallback_error_code': fallback_error_code}
    unsupported = _unsupported_limit(memory, cpu)
    if unsupported:
        return _refused({'exit_code': None, 'elapsed_ms': 0.0, 'deadline_seconds': deadline,
                         'stdout_bytes': 0, 'stderr_bytes': 0, 'output_truncated': False,
                         'limits': {'memory_bytes': memory, 'cpu_seconds': cpu}}, unsupported)
    base, out, err, timed_out, cancelled, spawn_error = _run_child(
        [sys.executable, '-c', _RUNNER], json.dumps(request, ensure_ascii=False), deadline, limit,
        grace, interval, _child_env(env), cwd, cancel_event, memory, cpu)
    if spawn_error:
        return _refused(base, spawn_error)
    if cancelled:
        return {**base, 'status': 'cancelled', 'error_code': 'TASK_CANCELLED',
                'stdout': out.text(), 'stderr': err.text()}
    if timed_out:
        return {**base, 'status': 'timeout', 'error_code': DEADLINE_ERROR_CODE,
                'stdout': out.text(), 'stderr': err.text()}
    try:
        payload = json.loads(out.text() or '')
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or 'ok' not in payload:
        return {**base, 'status': 'failed',
                'error_code': (_signal_code(base['exit_code'])
                               or (fallback_error_code if base['exit_code'] else 'TASK_RESULT_INVALID')),
                'stdout': out.text(), 'stderr': err.text()}
    if payload.get('ok'):
        return {**base, 'status': 'ok', 'value': payload.get('value')}
    return {**base, 'status': 'failed', 'error_code': payload.get('error_code') or fallback_error_code,
            'status_code': payload.get('status'), 'stdout': out.text(), 'stderr': err.text()}


# --------------------------------------------------------------------------------------
# Cooperative cross-host leases
#
# A lease is coordination, not consensus: it assumes a shared, healthy database and that
# every writer compares the `fence` it acquired against the fence currently stored before
# committing. A holder that lost its lease therefore cannot silently overwrite newer work.
# --------------------------------------------------------------------------------------

def _lease_name(name):
    if not isinstance(name, str) or not _LEASE_NAME.fullmatch(name):
        raise ValueError('TASK_BUDGET_LEASE_NAME_INVALID')
    return name


def _lease_ttl(ttl_seconds):
    if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= LEASE_TTL_MAX):
        raise ValueError('TASK_BUDGET_LEASE_TTL_INVALID')
    return ttl_seconds


def _lease_holder(holder):
    if not isinstance(holder, str) or not holder.strip() or len(holder) > 200:
        raise ValueError('TASK_BUDGET_LEASE_HOLDER_INVALID')
    return holder.strip()


def acquire_lease(dsn, name, ttl_seconds=LEASE_TTL_DEFAULT, *, holder):
    """Take a named lease when it is free or expired; returns None when it is held.

    A takeover increments ``fence``; callers must compare that number before writing.
    """
    name, ttl, holder = _lease_name(name), _lease_ttl(ttl_seconds), _lease_holder(holder)
    token = secrets.token_urlsafe(24)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''INSERT INTO task_leases(name,token,holder,fence,expires_at)
            VALUES (%s,%s,%s,1,clock_timestamp()+%s*interval '1 second')
            ON CONFLICT (name) DO UPDATE SET token=EXCLUDED.token,holder=EXCLUDED.holder,
                fence=task_leases.fence+1,acquired_at=clock_timestamp(),renewed_at=clock_timestamp(),
                expires_at=EXCLUDED.expires_at
            WHERE task_leases.expires_at<=clock_timestamp()
            RETURNING name,token,holder,fence,expires_at''', (name, token, holder, ttl))
        row = cur.fetchone()
    return dict(row) if row else None


def renew_lease(dsn, name, token, ttl_seconds=LEASE_TTL_DEFAULT):
    """Extend a lease the caller still holds; a taken-over lease cannot be renewed."""
    name, ttl = _lease_name(name), _lease_ttl(ttl_seconds)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''UPDATE task_leases SET expires_at=clock_timestamp()+%s*interval '1 second',
            renewed_at=clock_timestamp() WHERE name=%s AND token=%s AND expires_at>clock_timestamp()
            RETURNING fence,expires_at''', (ttl, name, token))
        row = cur.fetchone()
    return dict(row) if row else None


def release_lease(dsn, name, token):
    """Release a lease the caller still holds; False means it was already lost."""
    name = _lease_name(name)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM task_leases WHERE name=%s AND token=%s RETURNING name', (name, token))
        return cur.fetchone() is not None


def lease_holder(dsn, name):
    """Current holder of a lease, including whether it has already expired."""
    name = _lease_name(name)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT name,token,holder,fence,acquired_at,renewed_at,expires_at,
            expires_at<=clock_timestamp() AS expired FROM task_leases WHERE name=%s''', (name,))
        row = cur.fetchone()
    return dict(row) if row else None


def sweep_leases(dsn):
    """Delete expired lease rows and return their names (advisory cleanup only)."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM task_leases WHERE expires_at<=clock_timestamp() RETURNING name')
        return sorted(row[0] for row in cur.fetchall())
