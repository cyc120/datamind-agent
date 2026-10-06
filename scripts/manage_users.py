"""Provision or disable local accounts; passwords are entered through getpass."""
import argparse
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg2
from datamind.agent import _write_dsn
from datamind.auth import provision,ROLES


def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create');create.add_argument('username');create.add_argument('--name',required=True)
    create.add_argument('--role',action='append',choices=sorted(ROLES),required=True)
    disable=sub.add_parser('disable');disable.add_argument('username');args=ap.parse_args()
    if args.command=='create':
        password=getpass.getpass('密码（12–256 字符）：')
        if password!=getpass.getpass('再次输入密码：'):raise SystemExit('两次密码不同')
        try:provision(args.username,args.name,password,args.role,_write_dsn())
        except (ValueError,psycopg2.IntegrityError):raise SystemExit('账户创建失败：检查密码长度、账号格式或重复账号')
        print('账户已创建：'+args.username)
    else:
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE auth_users SET active=false WHERE username=%s RETURNING user_id',(args.username,))
            row=cur.fetchone()
            if not row:raise SystemExit('账户不存在')
            cur.execute('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s',(row[0],))
        print('账户已停用，会话已撤销：'+args.username)


if __name__=='__main__':main()
