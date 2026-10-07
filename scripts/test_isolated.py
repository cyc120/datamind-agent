"""Run regression against a disposable metadata clone, leaving demo state intact.

Requires the existing datamind-postgres container and initialized demo data.
Only datamind_meta connections in this process are redirected; retail/industrial
SQL stays read-only. Model settings and private PDF registry rows are excluded
from the database copy.
"""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
import uuid

import psycopg2
from psycopg2.extensions import parse_dsn

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pattern',default='test_*.py')
    ap.add_argument('--container',default='datamind-postgres');args=ap.parse_args()
    database='datamind_test_'+uuid.uuid4().hex[:12]
    original_connect=psycopg2.connect
    docker=['docker','exec',args.container]
    subprocess.run(docker+['createdb','-U','postgres','-O','datamind_app',database],check=True,capture_output=True)
    try:
        dump=subprocess.run(docker+['pg_dump','-U','postgres','-Fc','--exclude-table-data=app_settings',
                                   '--exclude-table-data=auth_users','--exclude-table-data=auth_sessions',
                                   '--exclude-table-data=auth_login_limits',
                                   '--exclude-table-data=source_authority_rules','--exclude-table-data=source_authority_events',
                                   '--exclude-table-data=cad_assets','--exclude-table-data=cad_versions',
                                   '--exclude-table-data=cad_association_reviews',
                                   '--exclude-table-data=dimension_measurements','--exclude-table-data=dimension_measurement_versions',
                                   '--exclude-table-data=dimension_bindings','--exclude-table-data=dimension_binding_versions',
                                   '--exclude-table-data=dimension_approval_events','--exclude-table-data=dimension_comparisons',
                                   '--exclude-table-data=file_jobs','--exclude-table-data=file_job_items',
                                   '--exclude-table-data=pdf_snapshot_assets','--exclude-table-data=pdf_snapshot_versions',
                                   'datamind_meta'],check=True,capture_output=True).stdout
        subprocess.run(['docker','exec','-i',args.container,'pg_restore','-U','postgres','-d',database],
                       input=dump,check=True,capture_output=True)

        def isolated_connect(dsn=None,*connect_args,**kwargs):
            options=parse_dsn(dsn) if dsn else {}
            options.update(kwargs)
            if options.get('dbname')=='datamind_meta':options['dbname']=database
            return original_connect(*connect_args,**options)

        psycopg2.connect=isolated_connect
        # Runtime APIs only accept repository-owned files. Adapt paths in the
        # clone when this command runs in a different checkout/worktree.
        from datamind.agent import _write_dsn
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT document_id,object_uri FROM document_assets')
            for document_id,uri in cur.fetchall():
                _,marker,suffix=(uri or '').partition('/data/')
                source=(ROOT/'data'/suffix).resolve() if marker else None
                if not source or not source.is_relative_to(ROOT/'data') or not source.is_file():
                    raise RuntimeError('Clone source unavailable: '+document_id)
                cur.execute('UPDATE document_assets SET object_uri=%s WHERE document_id=%s',(str(source),document_id))
            cur.execute("SELECT document_id,object_uri FROM document_assets WHERE file_type='pdf'")
            pdf_sources=cur.fetchall()
        import pymupdf
        for document_id,uri in pdf_sources:
            directory=ROOT/'data/object_store/page_images'/document_id;directory.mkdir(parents=True,exist_ok=True)
            with pymupdf.open(uri) as pdf:
                for i,page in enumerate(pdf):
                    (directory/f'page_{i+1:03d}.png').write_bytes(page.get_pixmap(dpi=110).tobytes('png'))
        # ORT's optional telemetry HTTP thread can race interpreter shutdown on
        # macOS. Keep regression local; this does not change model inference.
        try:
            import onnxruntime
        except ImportError:
            pass
        else:
            onnxruntime.disable_telemetry_events()
        runpy.run_path(str(ROOT/'db/seed_industrial.py'),run_name='__main__')
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=args.pattern)
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                              metadata_isolated=True,model_settings_copied=False,
                              private_pdf_snapshot_rows_copied=False,source_database_modified=False)))
        return 0 if result.wasSuccessful() else 1
    finally:
        psycopg2.connect=original_connect
        subprocess.run(docker+['dropdb','-U','postgres','--force',database],check=True,capture_output=True)


if __name__=='__main__':
    sys.path.insert(0,str(ROOT))
    raise SystemExit(main())
