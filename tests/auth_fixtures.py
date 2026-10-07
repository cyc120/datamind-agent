"""Disposable provisioned accounts; never use shared/default credentials."""
import secrets
import uuid
import psycopg2
from datamind.agent import _write_dsn
from datamind.auth import provision


def login(client,roles=('reviewer',),name='隔离测试'):
    username='fixture-'+uuid.uuid4().hex[:18];password=secrets.token_urlsafe(24)
    user_id=provision(username,name,password,list(roles),_write_dsn())
    client._fixture_users=getattr(client,'_fixture_users',[])+[user_id]
    response=client.post('/v1/auth/login',json={'username':username,'password':password})
    if response.status_code!=200:raise AssertionError(response.text)
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response.json()['user']


def cleanup(client):
    with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
        for user_id in getattr(client,'_fixture_users',[]):
            cur.execute('DELETE FROM auth_users WHERE user_id=%s',(user_id,))
