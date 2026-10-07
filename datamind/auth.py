"""Provisioned local identities, bounded sessions and role/CSRF checks."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import hmac
import os
import re
import secrets
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor

COOKIE = 'datamind_session'
SESSION_SECONDS = 8*60*60
ROLES = {'viewer','reviewer','approver','admin'}

# Local no-login demo: requests coming from this machine act as a provisioned admin so the
# operator need not type a password. Set DATAMIND_LOCAL_NO_LOGIN=0 to require login again,
# or =1 to force the bypass for non-loopback clients. Sessions, roles and CSRF stay in place.
DEMO_USER_ID = 'user-local-admin'
DEMO_USERNAME = 'local-admin'
DEMO_DISPLAY = '本机管理员'
DEMO_CSRF = secrets.token_urlsafe(32)
_LOOPBACK = {'127.0.0.1','::1','localhost'}


def demo_allowed(request):
    flag=os.environ.get('DATAMIND_LOCAL_NO_LOGIN')
    if flag is not None:
        return flag.strip().lower() not in {'','0','false','no','off'}
    return (request.client.host if request.client else '') in _LOOPBACK


def ensure_demo_user(dsn):
    """Idempotently keep a real admin row so audit columns and joins stay valid."""
    with psycopg2.connect(dsn) as conn,conn.cursor() as cur:
        cur.execute('''INSERT INTO auth_users(user_id,username,display_name,password_hash,roles)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (DEMO_USER_ID,DEMO_USERNAME,DEMO_DISPLAY,password_hash(secrets.token_urlsafe(24)),['admin']))
    return {'user_id':DEMO_USER_ID,'username':DEMO_USERNAME,'display_name':DEMO_DISPLAY,
            'roles':['admin'],'csrf_token':DEMO_CSRF,'_demo':True}


def _demo_identity(request,dsn,mutation):
    user=ensure_demo_user(dsn)
    if mutation:
        origin_check(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token','').encode(),user['csrf_token'].encode()):
            fail('CSRF_REQUIRED',403)
    return user


def fail(code, status=401):
    raise HTTPException(status, detail={'error_code':code})


def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise ValueError('PASSWORD_LENGTH_12_TO_256_REQUIRED')
    salt=secrets.token_bytes(16)
    value=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return 'scrypt$'+salt.hex()+'$'+value.hex()


def password_matches(password, encoded):
    try:
        algorithm,salt,value=encoded.split('$')
        if algorithm!='scrypt':return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return hmac.compare_digest(actual,bytes.fromhex(value))
    except (ValueError,TypeError):return False


@lru_cache(maxsize=1)
def dummy_password_hash():
    return password_hash(secrets.token_urlsafe(24))


def provision(username,display_name,password,roles,dsn):
    """Local administration only; there is no anonymous registration endpoint."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}',username):raise ValueError('INVALID_USERNAME')
    if not display_name.strip() or len(display_name)>120 or not roles or not set(roles)<=ROLES:raise ValueError('INVALID_USER_PROFILE')
    user_id='user-'+uuid.uuid4().hex[:24]
    with psycopg2.connect(dsn) as conn,conn.cursor() as cur:
        cur.execute('INSERT INTO auth_users(user_id,username,display_name,password_hash,roles) VALUES (%s,%s,%s,%s,%s)',
                    (user_id,username,display_name.strip(),password_hash(password),sorted(set(roles))))
    return user_id


def origin_check(request):
    origin=request.headers.get('origin')
    expected=str(request.base_url).rstrip('/')
    if (origin is not None and origin!=expected) or request.headers.get('sec-fetch-site')=='cross-site':
        fail('ORIGIN_NOT_ALLOWED',403)


def login(request,username,password,dsn):
    origin_check(request)
    # Both per-client and per-account windows prevent retries through aliases.
    client=request.client.host if request.client else 'unknown'
    buckets=sorted(hashlib.sha256(s.encode()).hexdigest() for s in ('client:'+client,'user:'+username.casefold()))
    token=None;blocked=False
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        for bucket in buckets:
            cur.execute('INSERT INTO auth_login_limits(bucket) VALUES (%s) ON CONFLICT DO NOTHING',(bucket,))
            cur.execute("UPDATE auth_login_limits SET failures=0,window_start=now() WHERE bucket=%s AND window_start<now()-interval '15 minutes'",(bucket,))
            cur.execute('SELECT failures FROM auth_login_limits WHERE bucket=%s FOR UPDATE',(bucket,))
            if cur.fetchone()['failures']>=10:blocked=True
        if not blocked:
            cur.execute('SELECT * FROM auth_users WHERE username=%s',(username,));user=cur.fetchone()
            matched=password_matches(password,user['password_hash'] if user else dummy_password_hash())
            if not user or not user['active'] or not matched:
                for bucket in buckets:cur.execute('UPDATE auth_login_limits SET failures=failures+1 WHERE bucket=%s',(bucket,))
            else:
                # Never store the browser's session credential in cleartext.
                token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                cur.execute('DELETE FROM auth_sessions WHERE expires_at<now() OR revoked_at IS NOT NULL')
                old=request.cookies.get(COOKIE)
                if old:cur.execute('UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s',(hashlib.sha256(old.encode()).hexdigest(),))
                cur.execute('INSERT INTO auth_sessions(token_hash,user_id,csrf_token,expires_at) VALUES (%s,%s,%s,%s)',
                            (hashlib.sha256(token.encode()).hexdigest(),user['user_id'],csrf,datetime.now(timezone.utc)+timedelta(seconds=SESSION_SECONDS)))
                # Clear the account window, retain the client's failed attempts.
                cur.execute('UPDATE auth_login_limits SET failures=0 WHERE bucket=%s',(hashlib.sha256(('user:'+username.casefold()).encode()).hexdigest(),))
    if blocked:fail('LOGIN_RATE_LIMITED',429)
    if not token:fail('LOGIN_FAILED')
    return token,dict(user_id=user['user_id'],username=user['username'],display_name=user['display_name'],roles=user['roles']),csrf


def current(request,dsn,*,required=False,permission=None,mutation=False):
    token=request.cookies.get(COOKIE)
    demo=demo_allowed(request)
    if not token:
        if demo:return _demo_identity(request,dsn,mutation)
        if required:fail('LOGIN_REQUIRED')
        return None
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT u.user_id,u.username,u.display_name,u.roles,s.csrf_token FROM auth_sessions s
            JOIN auth_users u USING(user_id) WHERE s.token_hash=%s AND s.revoked_at IS NULL
            AND s.expires_at>now() AND u.active''',(hashlib.sha256(token.encode()).hexdigest(),))
        user=cur.fetchone()
    if not user:
        if demo:return _demo_identity(request,dsn,mutation)
        if required:fail('SESSION_INVALID')
        return None
    user=dict(user)
    user['_session_hash']=hashlib.sha256(token.encode()).hexdigest()
    if permission and permission not in user['roles'] and 'admin' not in user['roles']:fail('PERMISSION_DENIED',403)
    if mutation:
        origin_check(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token','').encode(),user['csrf_token'].encode()):fail('CSRF_REQUIRED',403)
    return user


def lock_identity(cur,identity,permission):
    """Recheck and lock the session/account in the mutation transaction."""
    if identity.get('_demo'):
        if permission and permission not in identity['roles'] and 'admin' not in identity['roles']:
            fail('PERMISSION_DENIED',403)
        return {'user_id':identity['user_id'],'display_name':identity['display_name'],'roles':identity['roles']}
    cur.execute('''SELECT u.user_id,u.display_name,u.roles FROM auth_users u JOIN auth_sessions s USING(user_id)
        WHERE u.user_id=%s AND s.token_hash=%s AND u.active AND s.revoked_at IS NULL AND s.expires_at>now()
        FOR SHARE OF u,s''',(identity['user_id'],identity['_session_hash']))
    row=cur.fetchone()
    if not row:fail('SESSION_INVALID')
    if permission not in row['roles'] and 'admin' not in row['roles']:fail('PERMISSION_DENIED',403)
    return dict(row)


def logout(request,dsn):
    user=current(request,dsn,required=True,mutation=True)
    token=request.cookies.get(COOKIE)
    if token:
        with psycopg2.connect(dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s',
                        (hashlib.sha256(token.encode()).hexdigest(),))
    return user
