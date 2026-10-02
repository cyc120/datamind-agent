"""Introspect the bounded industrial query surface and register semantic metadata."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg2
from psycopg2.extras import Json
from db.migrate import migrate
from datamind.agent import _write_dsn

VERSION = 'industrial-catalog-v1'
TABLES = {
    'parts': ('零件目录', ['零件名称', '材料']),
    'measurement_observations': ('工业测量记录', ['测量', '温度', '批次', '工序'])
}
COLUMNS = {
    'part_id': ('零件编号', 'id', ['零件', 'A102', 'A103', 'A104']),
    'name': ('零件名称', 'dimension', ['名称']),
    'material': ('材料', 'dimension', []),
    'measurement_id': ('测量编号', 'id', ['测量数', '采样编号']),
    'batch_id': ('批次编号', 'id', ['批次', '批次数']),
    'produced_at': ('生产时间', 'time', ['年月', '月份']),
    'operation_no': ('工序号', 'dimension', ['工序']),
    'metric': ('测量指标', 'dimension', []),
    'value': ('原始温度', 'metric', ['原值']),
    'unit': ('原始单位', 'dimension', ['温度单位']),
    'measured_at': ('测量时间', 'time', []),
    'method': ('测量方法', 'dimension', ['测量口径']),
    'temperature_c': ('摄氏温度', 'metric', ['平均温度', '温度均值', '最高温度', '最低温度', '归一化'])
}
METRICS = [
    ('measured_batch_count', '已测量批次数', ['批次数', '多少个批次', '已测量批次'],
     'count(DISTINCT batch_id); only batches with measurements; grouping by part optional',
     'count(DISTINCT batch_id)', '批次', 'distinct batch_id'),
    ('measurement_count', '测量数', ['测量数', '多少条测量', '测量记录数'],
     'count(*) at measurement_id grain; multi-sample batch counted per sample', 'count(*)', '条', 'measurement_id'),
    ('mean_temperature_c', '平均温度', ['平均温度', '温度均值'],
     'avg(temperature_c); measurement grain; K minus 273.15; unknown units NULL and excluded',
     'avg(temperature_c)', 'degC', 'measurement_id')
]


def main():
    migrate()
    with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(714202611)')
        cur.execute('''SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnum,a.attnotnull
                       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                       JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                       WHERE n.nspname='demo_industrial' AND c.relname=ANY(%s) AND c.relkind IN ('r','v')
                       ORDER BY c.relname,a.attnum''', (list(TABLES),))
        columns = cur.fetchall()
        if {r[0] for r in columns} != set(TABLES):
            raise ValueError('INDUSTRIAL_SCHEMA_NOT_READY')
        contract = (ROOT/'data/industrial_demo/QUERY_CONTRACT.md').read_text()
        signature = hashlib.sha256(json.dumps([VERSION,columns,TABLES,COLUMNS,METRICS,contract],
                                    sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        snapshot = 'snap-industrial-' + signature[:20]
        cur.execute('''INSERT INTO data_sources(source_id,name,db_type,database_name,schema_allowlist,
                       table_allowlist,access_mode,environment,description)
                       VALUES ('demo_industrial','合成工业生产问数','postgresql','datamind_meta',
                       ARRAY['demo_industrial'],%s,'readonly','demo',%s)
                       ON CONFLICT(source_id) DO UPDATE SET database_name=EXCLUDED.database_name,
                       schema_allowlist=EXCLUDED.schema_allowlist,table_allowlist=EXCLUDED.table_allowlist,
                       access_mode=EXCLUDED.access_mode,description=EXCLUDED.description''',
                    (list(TABLES),VERSION+'; '+contract))
        cur.execute('SELECT 1 FROM schema_snapshots WHERE snapshot_id=%s', (snapshot,))
        if not cur.fetchone():
            cur.execute("SELECT coalesce(max(catalog_version),0)+1 FROM schema_snapshots WHERE source_id='demo_industrial'")
            number=cur.fetchone()[0]
            cur.execute('''INSERT INTO schema_snapshots(snapshot_id,source_id,catalog_version,table_count,column_count,notes)
                           VALUES (%s,'demo_industrial',%s,%s,%s,%s)''', (snapshot,number,len(TABLES),len(columns),VERSION+'; '+signature))
            for table,(name,aliases) in TABLES.items():
                cur.execute('''INSERT INTO schema_tables(snapshot_id,schema_name,table_name,business_name,aliases,table_comment)
                               VALUES (%s,'demo_industrial',%s,%s,%s,%s)''',
                            (snapshot,table,name,aliases,'Synthetic; '+('one measurement_id per row' if table!='parts' else 'one part_id per row')))
            for table,col,dtype,ordinal,notnull in columns:
                name,role,aliases=COLUMNS[col]
                enum={'degC':'摄氏度','K':'开尔文'} if col=='unit' else {'holding_temperature':'保温温度'} if col=='metric' else None
                cur.execute('''INSERT INTO schema_columns(snapshot_id,schema_name,table_name,column_name,ordinal,
                               data_type,is_nullable,is_primary_key,business_name,aliases,unit,semantic_role,enum_map)
                               VALUES (%s,'demo_industrial',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                            (snapshot,table,col,ordinal,dtype,not notnull,col==('part_id' if table=='parts' else 'measurement_id'),
                             name,aliases,'degC' if col=='temperature_c' else None,role,Json(enum) if enum else None))
            cur.execute('''INSERT INTO schema_relations(relation_id,snapshot_id,from_table,from_columns,to_table,
                           to_columns,cardinality,relation_kind,approved,description)
                           VALUES (%s,%s,'measurement_observations',ARRAY['part_id'],'parts',ARRAY['part_id'],
                           'n:1','business',TRUE,'测量所属零件；parts.part_id 唯一，无扇出')''', ('rel-ind-'+signature[:20],snapshot))
        for key,name,aliases,definition,expression,unit,grain in METRICS:
            cur.execute('''INSERT INTO business_metrics(metric_id,name,aliases,definition,expression_sql,unit,grain,source_id,status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'demo_industrial','active') ON CONFLICT(metric_id) DO UPDATE
                           SET definition=EXCLUDED.definition,expression_sql=EXCLUDED.expression_sql,aliases=EXCLUDED.aliases''',
                        ('demo_industrial.'+key,name,aliases,definition,expression,unit,grain))
    print(VERSION,snapshot,'tables=2 columns='+str(len(columns)))


if __name__=='__main__':main()
