"""Local governance, fail-closed source resolution and immutable run replay."""
import copy
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from psycopg2.extras import Json
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import _read_dsn, _write_dsn
from datamind.industrial import run
from datamind.authority import digest
import auth_fixtures

QUESTION='2026年9月A102工序30温度核验，生产记录和质检报告是否一致？'


class SourceAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.clients=[];self.runs=[];self.originals=[]
        for role in ('admin','reviewer','approver','viewer'):
            client=TestClient(app,client=('authority-'+uuid.uuid4().hex[:12],50000))
            auth_fixtures.login(client,roles=(role,),name='隔离'+role);self.clients.append(client);setattr(self,role,client)
        result=run(QUESTION,dsn=_read_dsn())
        self.source_row=next(r for r in result['sql_rows'] if r['measurement_id']=='M001-1')
        self.fact=next(f for f in result['facts'] if f.get('measurement_id')=='M001-1')
        self.doc_id=self.fact['document_id'];self.fact_url=f'/v1/documents/{self.doc_id}/facts/{self.fact["fact_id"]}'
        self.review()
        fact=self.current_fact()
        approval={**self.version(fact),'request_id':uuid.uuid4().hex,'review_id':fact['latest_review']['review_id'],
                  'previous_approval_id':None,'decision':'approved','reason':'隔离测试：先独立批准提取版本'}
        response=self.approver.post(self.fact_url+'/approvals',json=approval);self.assertEqual(response.status_code,200,response.text)

    def tearDown(self):
        ids=[uid for client in self.clients for uid in client._fixture_users]
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            for sql,args in reversed(self.originals):cur.execute(sql,args)
            cur.execute('DELETE FROM source_authority_events WHERE rule_id IN (SELECT rule_id FROM source_authority_rules WHERE creator_user_id=ANY(%s))',(ids,))
            cur.execute('DELETE FROM source_authority_rules WHERE creator_user_id=ANY(%s)',(ids,))
            cur.execute('DELETE FROM document_fact_approvals WHERE approver_user_id=ANY(%s)',(ids,))
            cur.execute('DELETE FROM document_fact_reviews WHERE reviewer_user_id=ANY(%s)',(ids,))
            for rid in self.runs:
                cur.execute('DELETE FROM evidence_edges WHERE run_id=%s',(rid,));cur.execute('DELETE FROM evidence_nodes WHERE run_id=%s',(rid,))
                cur.execute('DELETE FROM claims WHERE run_id=%s',(rid,));cur.execute('DELETE FROM runs WHERE run_id=%s',(rid,))
        for client in self.clients:auth_fixtures.cleanup(client);client.close()

    def version(self,fact):
        return {k:fact[k] for k in ('document_version','source_sha256','parser_version','payload_sha256')}

    def current_fact(self):
        return next(f for f in self.viewer.get(f'/v1/documents/{self.doc_id}/facts').json()['facts'] if f['fact_id']==self.fact['fact_id'])

    def review(self,decision='confirmed'):
        fact=self.current_fact()
        payload={**self.version(fact),'request_id':uuid.uuid4().hex,'previous_review_id':(fact.get('latest_review') or {}).get('review_id'),
                 'decision':decision,'reason':'隔离测试：按原文核对温度和上下文'}
        response=self.reviewer.post(self.fact_url+'/reviews',json=payload);self.assertEqual(response.status_code,200,response.text)

    def candidate(self):
        response=self.admin.get('/v1/authority/candidates',params={'question':QUESTION});self.assertEqual(response.status_code,200,response.text)
        return response.json()['candidates'][0]

    def registration(self,**changes):
        c=self.candidate()
        payload={'request_id':uuid.uuid4().hex,'question':QUESTION,'measurement_id':'M001-1','candidate_sha256':c['candidate_sha256'],
                 'selected_source':'report','valid_from':c['valid_from'],'valid_to':c['valid_to'],'basis':c['basis_suggestion'],
                 'rationale':'合成演示规则：引用依据页，按明确登记的范围采用报告；不代表真实工厂授权。'}
        payload.update(changes);return payload

    def register(self,**changes):
        response=self.admin.post('/v1/authority/rules',json=self.registration(**changes))
        self.assertEqual(response.status_code,200,response.text);return response.json()['rule']

    def event_payload(self,rule,decision='approved',**changes):
        records=self.viewer.get(f'/v1/authority/rules/{rule["rule_id"]}/events').json()['events']
        payload={'request_id':uuid.uuid4().hex,'decision':decision,'previous_event_id':records[-1]['event_id'] if records else None,
                 'reason':'隔离测试：独立检查登记范围与引用依据'}
        payload.update(changes);return payload

    def event(self,rule,decision='approved'):
        r=self.approver.post(f'/v1/authority/rules/{rule["rule_id"]}/events',json=self.event_payload(rule,decision))
        self.assertEqual(r.status_code,200,r.text);return r.json()['event']

    def result(self):return run(QUESTION,dsn=_read_dsn())
    def row(self):return next(r for r in self.result()['rows'] if r['measurement_id']=='M001-1')

    def test_roles_csrf_server_identity_and_self_approval(self):
        payload=self.registration()
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get('/v1/authority/rules').status_code,401)
            self.assertEqual(anonymous.post('/v1/authority/rules',json=payload).status_code,401)
        for client in (self.viewer,self.reviewer,self.approver):
            self.assertEqual(client.post('/v1/authority/rules',json=payload).status_code,403)
        token=self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.post('/v1/authority/rules',json=payload).status_code,403)
        self.admin.headers['X-CSRF-Token']=token
        self.assertEqual(self.admin.post('/v1/authority/rules',json={**payload,'creator_user_id':'spoof'}).status_code,422)
        rule=self.register();url=f'/v1/authority/rules/{rule["rule_id"]}/events'
        self.assertEqual(self.admin.post(url,json=self.event_payload(rule)).status_code,409)
        self.assertEqual(self.reviewer.post(url,json=self.event_payload(rule)).status_code,403)

    def test_pending_and_rejected_rules_never_resolve(self):
        rule=self.register();self.assertEqual(self.row()['agreement'],'unresolved')
        self.event(rule,'rejected');self.assertEqual(self.row()['agreement'],'unresolved')

    def test_report_selection_preserves_raw_values_and_original_compliance(self):
        rule=self.register();event=self.event(rule);row=self.row()
        self.assertEqual((row['agreement'],row['selected_value'],row['selected_unit']),('resolved_by_authority','850','degC'))
        self.assertEqual(row['selected_fact_ref']['kind'],'document_fact')
        self.assertEqual(row['authority']['event_id'],event['event_id'])
        self.assertEqual(row['compliance'],'out_of_range')
        self.assertEqual((row['value'],row['report_value']),(self.source_row['value'],'850'))
        self.assertEqual(self.current_fact()['approval_status'],self.fact['approval_status'])
        self.assertEqual(self.result()['summary']['authority_resolved'],['M001-1'])
        self.assertFalse(self.result()['model_used'])

    def test_production_selection_is_explicit_and_does_not_change_source_sql(self):
        rule=self.register(selected_source='production');self.event(rule);row=self.row()
        self.assertEqual((row['selected_value'],row['selected_fact_ref']['kind']),(self.source_row['value'],'sql_measurement'))
        self.assertEqual(row['report_value'],'850')
        with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
            cur.execute("SELECT value FROM demo_industrial.process_measurements WHERE measurement_id='M001-1'")
            self.assertEqual(str(cur.fetchone()[0]),self.source_row['value'])

    def test_revocation_restores_unresolved_and_cannot_be_bypassed(self):
        rule=self.register()
        url=f'/v1/authority/rules/{rule["rule_id"]}/events'
        approved=self.event_payload(rule);r=self.approver.post(url,json=approved);self.assertEqual(r.status_code,200)
        self.event(rule,'revoked');self.assertEqual(self.row()['agreement'],'unresolved')
        self.assertEqual(self.approver.post(url,json=approved).status_code,200)
        self.assertEqual(self.row()['agreement'],'unresolved')
        self.assertEqual([e['decision'] for e in self.viewer.get(url).json()['events']],['approved','revoked'])

    def test_registration_validity_basis_and_scope_validation(self):
        original=self.registration()
        for changes,status in [({'valid_from':'2026-09-01'},422),({'valid_to':original['valid_from']},422),
            ({'valid_from':'2027-01-01T00:00:00+08:00','valid_to':'2027-02-01T00:00:00+08:00'},422),
            ({'basis':{**original['basis'],'page_no':999}},409),({'basis':{**original['basis'],'source_sha256':'0'*64}},409),
            ({'measurement_id':'M006-2'},409),({'candidate_sha256':'0'*64},409),({'selected_source':'automatic'},422),
            ({'rationale':' '*12},422)]:
            r=self.admin.post('/v1/authority/rules',json={**original,**changes});self.assertEqual(r.status_code,status,r.text)

    def test_report_must_have_current_independent_approval(self):
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM document_fact_approvals WHERE approval_id=%s',(self.current_fact()['latest_approval']['approval_id'],))
        self.assertFalse(self.candidate()['eligible'])
        r=self.admin.post('/v1/authority/rules',json=self.registration());self.assertEqual(r.status_code,409)

    def test_new_review_blocks_activation_and_existing_resolution(self):
        rule=self.register();self.event(rule);self.review()
        self.assertEqual(self.row()['agreement'],'insufficient_evidence')
        url=f'/v1/authority/rules/{rule["rule_id"]}/events'
        self.assertEqual(self.approver.post(url,json=self.event_payload(rule)).status_code,409)

    def test_basis_acl_change_invalidates_active_rule(self):
        docs=self.viewer.get('/v1/documents').json()['documents']
        doc=next(d for d in docs if d['file_type']=='pdf' and d['document_id']!=self.doc_id)
        basis={'document_id':doc['document_id'],'document_version':doc['doc_version'],'source_sha256':doc['sha256'],'page_no':1}
        rule=self.register(basis=basis);self.event(rule)
        original=self.viewer.get('/v1/documents/'+doc['document_id']).json()
        self.originals.append(('UPDATE document_assets SET acl=%s WHERE document_id=%s',(original['acl'],doc['document_id'])))
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:cur.execute("UPDATE document_assets SET acl=ARRAY['private'] WHERE document_id=%s",(doc['document_id'],))
        row=self.row();self.assertEqual(row['agreement'],'unresolved')
        self.assertTrue(any(i['reason']=='AUTHORITY_RULE_STALE' for i in row['issues']))

    def test_changed_sql_snapshot_blocks_activation_and_resolution(self):
        rule=self.register();self.event(rule)
        self.originals.append(("UPDATE demo_industrial.process_measurements SET value=%s WHERE measurement_id='M001-1'",(self.source_row['value'],)))
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:cur.execute("UPDATE demo_industrial.process_measurements SET value=861 WHERE measurement_id='M001-1'")
        self.assertEqual(self.row()['agreement'],'unresolved')
        self.assertEqual(self.approver.post(f'/v1/authority/rules/{rule["rule_id"]}/events',json=self.event_payload(rule)).status_code,409)

    def test_overlapping_rules_fail_closed_without_priority(self):
        a=self.register();self.event(a);b=self.register(selected_source='production');self.event(b)
        row=self.row();self.assertEqual(row['agreement'],'unresolved');self.assertNotIn('selected_fact_ref',row)
        self.assertTrue(any(i['reason']=='AUTHORITY_RULE_OVERLAP' for i in row['issues']))
        self.event(b,'revoked');self.assertEqual(self.row()['agreement'],'resolved_by_authority')

    def test_hash_tampering_and_readonly_registry_privileges(self):
        rule=self.register();self.event(rule);changed=copy.deepcopy(rule['payload']);changed['selected_source']='production'
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE source_authority_rules SET payload=%s WHERE rule_id=%s',(Json(changed),rule['rule_id']))
        self.assertEqual(self.row()['agreement'],'unresolved')
        self.assertEqual(self.approver.post(f'/v1/authority/rules/{rule["rule_id"]}/events',json=self.event_payload(rule)).status_code,409)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute("UPDATE source_authority_rules SET payload='{}'::jsonb WHERE rule_id=%s",(rule['rule_id'],))
        self.assertEqual(self.row()['agreement'],'unresolved')
        self.event(rule,'revoked')
        with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
            for table in ('source_authority_rules','source_authority_events'):
                cur.execute('SELECT has_table_privilege(current_user,%s,\'SELECT\'),has_table_privilege(current_user,%s,\'INSERT,UPDATE,DELETE\')',(table,table))
                self.assertEqual(cur.fetchone(),(True,False))

    def test_idempotency_concurrent_requests_and_stale_previous_state(self):
        payload=self.registration()
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:self.admin.post('/v1/authority/rules',json=payload),range(2)))
        self.assertEqual([r.status_code for r in responses],[200,200]);rule=responses[0].json()['rule']
        self.assertEqual(rule['rule_id'],responses[1].json()['rule']['rule_id'])
        self.assertEqual(self.admin.post('/v1/authority/rules',json={**payload,'selected_source':'production'}).status_code,409)
        body=self.event_payload(rule);url=f'/v1/authority/rules/{rule["rule_id"]}/events'
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:self.approver.post(url,json=body),range(2)))
        self.assertEqual([r.status_code for r in responses],[200,200]);self.assertEqual(len(self.viewer.get(url).json()['events']),1)
        self.assertEqual(self.approver.post(url,json={**body,'request_id':uuid.uuid4().hex}).status_code,409)

    def test_run_evidence_retains_rule_snapshot_after_revocation(self):
        rule=self.register();self.event(rule)
        response=self.viewer.post('/v1/chat',json={'question':QUESTION,'mode':'industrial'});self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.runs.append(data['run_id']);rid=data['run_id']
        conflicts=self.viewer.get(f'/v1/runs/{rid}/conflicts').json()['conflicts']
        conflict=next(c for c in conflicts if c['conflict_type']=='value_conflict')
        self.assertEqual(conflict['resolution_status'],'resolved_by_authority')
        self.assertEqual(conflict['selected_fact_ref']['kind'],'document_fact')
        self.assertEqual(conflict['authority']['rule_sha256'],digest(rule['payload']))
        evidence=self.viewer.get(f'/v1/runs/{rid}/evidence').json()
        self.assertIn('AuthorityRule',{n['node_type'] for n in evidence['nodes']})
        self.event(rule,'revoked');self.assertEqual(self.row()['agreement'],'unresolved')
        replay=self.viewer.get(f'/v1/runs/{rid}/events').json()['events'][-1]['output_summary']['industrial_result']
        self.assertEqual(replay,data['industrial_result'])
        self.assertEqual(self.viewer.get(f'/v1/runs/{rid}/conflicts').json()['conflicts'],conflicts)


if __name__=='__main__':unittest.main()
