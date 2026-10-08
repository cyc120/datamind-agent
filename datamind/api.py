"""Local API for SQL/document agents, source files and persisted evidence."""
from __future__ import annotations

import json
import hashlib
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import psycopg2
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from .agent import DataMindAgent, _read_dsn, _write_dsn
from .knowledge import COLLECTIONS, ROOT
from .pdf_publications import RegistrationRequest as PdfSnapshotRegistrationRequest
from .pdf_index import PublicationRequest as PdfKnowledgePublicationRequest
from .pdf_search import SearchRequest as PdfKnowledgeSearchRequest
from .providers import (OPENAI, PROTOCOLS, list_models as provider_list_models,
                        test_chat as provider_test_chat)
from .settings_store import (DEFAULT_BASE_URL, DEFAULT_MODEL, PROFILE_FIELDS, activate_profile,
                             active_profile, clear_model_config, delete_profile, list_profiles,
                             load_model_config, normalize_base_url, public_model_config,
                             public_profile, public_profiles, save_model_config, save_profile)


class DocumentReference(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    document_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    document_version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser_version: str = Field(min_length=1,max_length=64)


class Scope(BaseModel):
    model_config = {'extra':'forbid'}
    database_sources: list[str] | None = None
    document_collections: list[str] | None = None
    document_refs: list[DocumentReference] | None = Field(default=None,min_length=1,max_length=8)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    scope: Scope | None = None
    mode: Literal["auto", "sql", "rag", "industrial"] = "auto"


class FactReviewRequest(BaseModel):
    model_config = {'extra':'forbid'}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    document_version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser_version: str = Field(min_length=1,max_length=64)
    payload_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    previous_review_id: str | None = Field(default=None,max_length=64)
    decision: Literal['confirmed','rejected','needs_review']
    reviewer_label: str | None = Field(default=None,min_length=1,max_length=120)
    reason: str = Field(min_length=1,max_length=1000)


class FactApprovalRequest(BaseModel):
    model_config = {'extra':'forbid'}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    document_version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser_version: str = Field(min_length=1,max_length=64)
    payload_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    review_id: str = Field(min_length=1,max_length=64)
    previous_approval_id: str | None = Field(default=None,max_length=64)
    decision: Literal['approved','rejected','revoked']
    reason: str = Field(min_length=1,max_length=1000)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=256)


class DrawingRegistrationRequest(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    document_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    title: str = Field(min_length=1,max_length=200)
    source_path: str = Field(min_length=1,max_length=500)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    provenance: Literal['synthetic_demo','authorized_external']
    previous_version: int = Field(ge=0)
    previous_source_sha256: str | None = Field(pattern=r'^[a-f0-9]{64}$')


class AuthorityBasis(BaseModel):
    model_config = {'extra':'forbid'}
    document_id: str = Field(min_length=1,max_length=64)
    document_version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    page_no: int = Field(ge=1)


class DrawingAssociationReviewRequest(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    document_version: int = Field(ge=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    parser_version: str = Field(min_length=1,max_length=64)
    bundle_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    feature_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    previous_review_id: str | None = Field(default=None,max_length=64)
    decision: Literal['confirmed','rejected','needs_review']
    selected_target: str | None = Field(default=None,pattern=r'^[1-9A-F][0-9A-F]*$',max_length=32)
    reason: str = Field(min_length=1,max_length=1000)


class AuthorityRequest(BaseModel):
    model_config = {'extra':'forbid'}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    question: str = Field(min_length=1,max_length=2000)
    measurement_id: str = Field(min_length=1,max_length=64)
    candidate_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    selected_source: Literal['production','report']
    valid_from: str = Field(min_length=1,max_length=50)
    valid_to: str = Field(min_length=1,max_length=50)
    basis: AuthorityBasis
    rationale: str = Field(min_length=10,max_length=1000)


class AuthorityEventRequest(BaseModel):
    model_config = {'extra':'forbid'}
    request_id: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    previous_event_id: str | None = Field(default=None,max_length=64)
    decision: Literal['approved','rejected','revoked']
    reason: str = Field(min_length=1,max_length=1000)


class ProfileRequest(BaseModel):
    """接入配置（provider profile）；protocol 决定走 OpenAI 兼容还是 Anthropic 协议。"""
    id: str | None = Field(default=None, max_length=64)
    profile_id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    protocol: str | None = Field(default=None, max_length=24)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    max_tokens: int | None = Field(default=None, ge=0, le=200000)
    timeout_s: float | None = Field(default=None, ge=0, le=600)
    auth_style: str | None = Field(default=None, max_length=24)
    headers: dict | None = None
    proxy: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=500)
    activate: bool | None = None


class ProfileImportRequest(BaseModel):
    profiles: list[dict]
    activate_first: bool = False


app = FastAPI(title="DataMind Agent", version="0.26.0")


@app.middleware('http')
async def identity_permissions(request: Request, call_next):
    path=request.url.path
    authority_path=path.startswith('/v1/authority/')
    cad_path=path=='/v1/drawings' or path.startswith('/v1/drawings/')
    dimension_path=path.startswith('/v1/dimensions/')
    files_path=path.startswith('/v1/files/')
    pdf_snapshot_path=path=='/v1/pdf-snapshots' or path.startswith('/v1/pdf-snapshots/')
    pdf_knowledge_path=path=='/v1/pdf-knowledge' or path.startswith('/v1/pdf-knowledge/')
    plan_path=path.startswith('/v1/plans')
    permission=('admin' if plan_path or files_path or pdf_snapshot_path or pdf_knowledge_path else
                'approver' if dimension_path and request.method=='POST' and path.endswith('/approvals') else
                'admin' if dimension_path and (request.method=='POST' or path.endswith('/sources')) else
                'reviewer' if cad_path and request.method=='POST' and path.endswith('/reviews') else
                'admin' if path=='/v1/drawings/sources' or (cad_path and request.method=='POST') else
                'admin' if path.startswith('/v1/model/') or path=='/v1/authority/candidates' or
                (authority_path and request.method=='POST' and path.endswith('/rules')) else
                'approver' if authority_path and request.method=='POST' and path.endswith('/events') else
                'reviewer' if request.method=='POST' and path.endswith('/reviews') else
                'approver' if request.method=='POST' and path.endswith('/approvals') else None)
    if permission or authority_path or cad_path or dimension_path or pdf_snapshot_path or pdf_knowledge_path or plan_path:
        from .auth import current
        try:
            request.state.identity=await run_in_threadpool(current,request,_write_dsn(),required=True,
                permission=permission,mutation=request.method not in {'GET','HEAD','OPTIONS'})
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code,content={'detail':exc.detail},
                                headers={'Cache-Control':'no-store'} if pdf_snapshot_path or pdf_knowledge_path else None)
    response=await call_next(request)
    if cad_path or dimension_path or files_path or pdf_snapshot_path or pdf_knowledge_path or plan_path:response.headers['Cache-Control']='no-store'
    return response


def _pdf_snapshot_recheck(request):
    # File verification can outlive the middleware's initial identity check.
    from .auth import current
    current(request,_write_dsn(),required=True,permission='admin')


def _admin_recheck(request):
    # Long-running work can outlive the middleware's initial identity check.
    from .auth import current
    current(request,_write_dsn(),required=True,permission='admin')


@app.post('/v1/pdf-snapshots')
def pdf_snapshot_register(payload: PdfSnapshotRegistrationRequest,request: Request):
    from .pdf_publications import register
    version=jsonable_encoder(register(payload.model_dump(),_write_dsn(),request.state.identity))
    _pdf_snapshot_recheck(request)
    return {'version':version,
            'semantics':'Registered parsing snapshot; not indexed, knowledge-visible, or source-authority approval'}


@app.get('/v1/pdf-snapshots')
def pdf_snapshots(request: Request,limit: int=Query(default=20,ge=1,le=50),offset: int=Query(default=0,ge=0,le=10000)):
    from .pdf_publications import list_assets
    result=jsonable_encoder(list_assets(_write_dsn(),limit=limit,offset=offset))
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/pdf-snapshots/{document_id}/versions')
def pdf_snapshot_versions(document_id: str,request: Request,limit: int=Query(default=20,ge=1,le=50),
                          offset: int=Query(default=0,ge=0,le=10000)):
    from .pdf_publications import history
    result=jsonable_encoder(history(document_id,_write_dsn(),limit=limit,offset=offset))
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/pdf-snapshots/{document_id}')
def pdf_snapshot_detail(document_id: str,request: Request,version: int | None=Query(default=None,ge=1)):
    from .pdf_publications import read_version,public_version
    version_record=jsonable_encoder(public_version(read_version(document_id,_write_dsn(),version)))
    _pdf_snapshot_recheck(request)
    return {'version':version_record}


def _pdf_snapshot_selected(document_id,version,expected_manifest_sha256):
    from .pdf_publications import read_version
    row=read_version(document_id,_write_dsn(),version)
    if row['manifest_sha256']!=expected_manifest_sha256:
        raise HTTPException(409,detail={'error_code':'PDF_SNAPSHOT_SELECTION_CHANGED'})
    return row


@app.get('/v1/pdf-snapshots/{document_id}/file')
def pdf_snapshot_file(document_id: str,request: Request,version: int=Query(ge=1),
                      expected_manifest_sha256: str=Query(pattern=r'^[a-f0-9]{64}$')):
    from .pdf_publications import read_artifact
    row=_pdf_snapshot_selected(document_id,version,expected_manifest_sha256)
    data=read_artifact(row,'source')
    _pdf_snapshot_recheck(request)
    return Response(data,media_type='application/pdf',
                    headers={'Content-Disposition':f'attachment; filename="document-v{row["document_version"]}.pdf"',
                             'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})


@app.get('/v1/pdf-snapshots/{document_id}/pages/{page_no}')
def pdf_snapshot_page(document_id: str,page_no: int,request: Request,version: int=Query(ge=1),
                      expected_manifest_sha256: str=Query(pattern=r'^[a-f0-9]{64}$')):
    from .pdf_publications import read_artifact
    row=_pdf_snapshot_selected(document_id,version,expected_manifest_sha256)
    data=read_artifact(row,'page_image',page_no)
    _pdf_snapshot_recheck(request)
    return Response(data,media_type='image/png',
                    headers={'X-Content-Type-Options':'nosniff'})


@app.get('/v1/pdf-snapshots/{document_id}/bundle')
def pdf_snapshot_bundle(document_id: str,request: Request,version: int=Query(ge=1),
                        expected_manifest_sha256: str=Query(pattern=r'^[a-f0-9]{64}$')):
    from .pdf_publications import read_artifact,public_version
    row=_pdf_snapshot_selected(document_id,version,expected_manifest_sha256)
    result={'version':jsonable_encoder(public_version(row)),
            'bundle':json.loads(read_artifact(row,'bundle'))}
    _pdf_snapshot_recheck(request)
    return result


@app.post('/v1/pdf-knowledge/publications')
def pdf_knowledge_publish(payload: PdfKnowledgePublicationRequest,request: Request):
    from .pdf_index import submit
    result=jsonable_encoder(submit(payload.model_dump(),_write_dsn(),request.state.identity))
    _pdf_snapshot_recheck(request)
    return {'publication':result}


@app.get('/v1/pdf-knowledge/publications')
def pdf_knowledge_publications(request: Request,limit: int=Query(default=20,ge=1,le=50),
                               offset: int=Query(default=0,ge=0,le=10000)):
    from .pdf_index import listing
    result=jsonable_encoder(listing(_write_dsn(),limit=limit,offset=offset))
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/pdf-knowledge/publications/{publication_id}')
def pdf_knowledge_publication(publication_id: str,request: Request):
    from .pdf_index import poll
    result=jsonable_encoder(poll(publication_id,_write_dsn()))
    _pdf_snapshot_recheck(request)
    return {'publication':result}


@app.post('/v1/pdf-knowledge/publications/{publication_id}/retry')
def pdf_knowledge_retry(publication_id: str,request: Request):
    from .pdf_index import retry
    result=jsonable_encoder(retry(publication_id,_write_dsn(),request.state.identity))
    _pdf_snapshot_recheck(request)
    return {'publication':result}


@app.get('/v1/pdf-knowledge/publications/{publication_id}/chunks')
def pdf_knowledge_chunks(publication_id: str,request: Request,
                          expected_plan_sha256: str=Query(pattern=r'^[a-f0-9]{64}$')):
    from .pdf_index import read_chunks
    result=jsonable_encoder(read_chunks(publication_id,_write_dsn()))
    if result['publication']['plan_sha256']!=expected_plan_sha256:
        raise HTTPException(409,detail={'error_code':'PDF_INDEX_SELECTION_CHANGED'})
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/pdf-knowledge/documents/{document_id}')
def pdf_knowledge_current(document_id: str,request: Request):
    from .pdf_index import current
    result=jsonable_encoder(current(document_id,_write_dsn()))
    _pdf_snapshot_recheck(request)
    return {'publication':result}


@app.post('/v1/pdf-knowledge/search')
def pdf_knowledge_search(payload: PdfKnowledgeSearchRequest, request: Request):
    from .pdf_search import search
    result = jsonable_encoder(search(payload.model_dump(), _write_dsn()))
    _pdf_snapshot_recheck(request)
    return result


@app.post('/v1/pdf-knowledge/answer')
def pdf_knowledge_answer(payload: PdfKnowledgeSearchRequest, request: Request):
    from .pdf_answer import answer
    result = jsonable_encoder(answer(payload.model_dump(), _write_dsn()))
    _pdf_snapshot_recheck(request)
    return result


@app.post('/v1/plans/execute')
def vcar_plan_execute(payload: dict, request: Request):
    from .vcar import run_request
    result = jsonable_encoder(run_request(payload, _write_dsn()))
    _admin_recheck(request)
    return result


@app.post('/v1/pdf-knowledge/publications/{publication_id}/repair')
def pdf_knowledge_repair(publication_id: str, request: Request):
    from .pdf_maintenance import repair_publication
    result = jsonable_encoder(repair_publication(publication_id, _write_dsn()))
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/pdf-knowledge/orphans')
def pdf_knowledge_orphans(request: Request):
    from .pdf_maintenance import orphan_scan
    result = jsonable_encoder(orphan_scan(_write_dsn()))
    _pdf_snapshot_recheck(request)
    return result


@app.post('/v1/pdf-knowledge/orphans/reclaim')
def pdf_knowledge_reclaim(payload: dict, request: Request):
    from .pdf_maintenance import reclaim_orphans, reclaim_payload
    body = reclaim_payload(payload)
    result = jsonable_encoder(reclaim_orphans(body['collection'], _write_dsn(), confirm=body['confirm']))
    _pdf_snapshot_recheck(request)
    return result


@app.get('/v1/drawings/sources')
def drawing_sources():
    from .cad_store import sources
    return {'sources':sources()}


@app.post('/v1/drawings')
def register_drawing(payload: DrawingRegistrationRequest,request: Request):
    from .cad_store import register
    return {'version':jsonable_encoder(register(payload.model_dump(),_write_dsn(),request.state.identity)),
            'semantics':'Versioned CAD inspection registration; not formal approval or production binding'}


@app.get('/v1/drawings')
def drawings():
    from .cad_store import list_assets
    return {'drawings':jsonable_encoder(list_assets(_read_dsn()))}


@app.get('/v1/drawings/{document_id}/versions')
def drawing_versions(document_id: str):
    from .cad_store import history
    return jsonable_encoder(history(document_id,_read_dsn()))


@app.get('/v1/drawings/{document_id}')
def drawing_detail(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version,public_version
    row=read_version(document_id,_read_dsn(),version)
    return {'version':jsonable_encoder(public_version(row)),'source':row['bundle']['snapshot']['source'],
            'identity':row['bundle']['snapshot']['identity'],
            'metadata':{k:v for k,v in row['bundle']['metadata'].items() if k!='render_entities'}}


@app.get('/v1/drawings/{document_id}/entities')
def drawing_entities(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version,public_version
    row=read_version(document_id,_read_dsn(),version)
    snapshot=row['bundle']['snapshot']
    return {'version':jsonable_encoder(public_version(row)),'entities':snapshot['entities'],'views':snapshot['views']}


@app.get('/v1/drawings/{document_id}/features')
def drawing_features(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version,public_version
    from .cad_reviews import inspect
    from ingestion.cad_contract import eligible_features
    row=read_version(document_id,_read_dsn(),version);snapshot=row['bundle']['snapshot']
    context={k:f['value'] for k,f in snapshot['identity'].items()};context['source_sha256']=row['source_sha256']
    return {'version':jsonable_encoder(public_version(row)),'features':snapshot['features'],
            'inspection_gate':eligible_features(snapshot,context),
            **jsonable_encoder(inspect(row,_read_dsn())),
            'semantics':'Original extraction/object consistency; association reviews are separate, without formal approval or production binding'}


@app.get('/v1/drawings/{document_id}/features/{feature_id}/reviews')
def drawing_review_history(document_id: str,feature_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version
    from .cad_reviews import history
    return jsonable_encoder(history(read_version(document_id,_read_dsn(),version),feature_id,_read_dsn()))


@app.post('/v1/drawings/{document_id}/features/{feature_id}/reviews')
def drawing_review_record(document_id: str,feature_id: str,payload: DrawingAssociationReviewRequest,request: Request):
    from .cad_reviews import record
    return {'review':jsonable_encoder(record(document_id,feature_id,payload.model_dump(),_write_dsn(),request.state.identity))}


@app.get('/v1/drawings/{document_id}/file')
def drawing_file(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version
    row=read_version(document_id,_read_dsn(),version)
    return Response(row['_source_bytes'],media_type='application/dxf',
                    headers={'Content-Disposition':f'attachment; filename="drawing-v{row["document_version"]}.dxf"',
                             'X-Content-Type-Options':'nosniff'})


@app.get('/v1/drawings/{document_id}/preview')
def drawing_preview(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version
    from ingestion.cad_render import render_svg
    row=read_version(document_id,_read_dsn(),version)
    return Response(render_svg(row['bundle']),media_type='image/svg+xml',
                    headers={'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'",
                             'X-Content-Type-Options':'nosniff'})


@app.get('/v1/drawings/{document_id}/viewer')
def drawing_viewer(document_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_store import read_version
    from ingestion.cad_render import render_html
    row=read_version(document_id,_read_dsn(),version)
    return Response(render_html(row['bundle']),media_type='text/html',
                    headers={'Content-Security-Policy':"sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
                             'X-Content-Type-Options':'nosniff'})


from .cad_dimensions import MeasurementRegistration,BindingRegistration,ApprovalRequest,ComparisonRequest


@app.get('/v1/dimensions/sources')
def dimension_sources():
    from .cad_dimensions import sources
    return {'sources':sources()}


@app.get('/v1/dimensions/measurements')
def dimension_measurements():
    from .cad_dimensions import listing
    return listing('measurements',_read_dsn())


@app.post('/v1/dimensions/measurements')
def dimension_measurement_register(payload: MeasurementRegistration,request: Request):
    from .cad_dimensions import register_measurement
    return {'measurement':register_measurement(payload.model_dump(),_write_dsn(),request.state.identity)}


@app.get('/v1/dimensions/measurements/{measurement_id}')
def dimension_measurement_detail(measurement_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_dimensions import read_measurement
    return {'measurement':read_measurement(measurement_id,_read_dsn(),version)}


@app.get('/v1/dimensions/bindings')
def dimension_bindings():
    from .cad_dimensions import listing
    return listing('bindings',_read_dsn())


@app.post('/v1/dimensions/bindings')
def dimension_binding_register(payload: BindingRegistration,request: Request):
    from .cad_dimensions import register_binding
    return {'binding':register_binding(payload.model_dump(),_write_dsn(),request.state.identity)}


@app.get('/v1/dimensions/bindings/{binding_id}')
def dimension_binding_detail(binding_id: str,version: int | None=Query(default=None,ge=1)):
    from .cad_dimensions import read_binding
    return read_binding(binding_id,_read_dsn(),version)


@app.post('/v1/dimensions/bindings/{binding_id}/approvals')
def dimension_approval_register(binding_id: str,payload: ApprovalRequest,request: Request):
    from .cad_dimensions import record_approval
    return {'approval':record_approval(binding_id,payload.model_dump(),_write_dsn(),request.state.identity)}


@app.post('/v1/dimensions/bindings/{binding_id}/comparisons')
def dimension_comparison_register(binding_id: str,payload: ComparisonRequest,request: Request):
    from .cad_dimensions import comparison
    return comparison(binding_id,payload.model_dump(),_write_dsn(),request.state.identity)


@app.get('/v1/dimensions/bindings/{binding_id}/comparisons')
def dimension_comparison_history(binding_id: str,offset: int=Query(default=0,ge=0,le=10000),
                                 limit: int=Query(default=20,ge=1,le=50)):
    from .cad_dimensions import comparison_history
    return comparison_history(binding_id,_read_dsn(),offset,limit)


@app.get('/v1/dimensions/comparisons/{comparison_id}')
def dimension_comparison_replay(comparison_id: str):
    from .cad_dimensions import replay
    return replay(comparison_id,_read_dsn())


from .file_jobs import FileJobRequest


@app.post('/v1/files/uploads')
async def file_upload(request: Request,filename: str=Query(min_length=1,max_length=200),
                      provenance: Literal['synthetic_demo','authorized_external']=Query()):
    from .file_uploads import stage
    return {'source':await stage(request,filename,provenance,_write_dsn(),request.state.identity)}


@app.post('/v1/files/jobs')
def file_job_submit(payload: FileJobRequest,request: Request):
    from .file_jobs import submit
    return submit(payload.model_dump(),_write_dsn(),request.state.identity)


@app.get('/v1/files/jobs')
def file_job_listing(request: Request):
    from .file_jobs import listing
    return {'jobs':listing(_write_dsn(),request.state.identity)}


@app.get('/v1/files/jobs/{job_id}')
def file_job_detail(job_id: str,request: Request):
    from .file_jobs import poll
    return poll(job_id,_write_dsn(),request.state.identity)


@app.post('/v1/files/jobs/{job_id}/cancel')
def file_job_cancel(job_id: str,request: Request):
    from .file_jobs import cancel
    return cancel(job_id,_write_dsn(),request.state.identity)


@app.post('/v1/files/jobs/{job_id}/retry')
def file_job_retry(job_id: str,request: Request):
    from .file_jobs import retry
    return retry(job_id,_write_dsn(),request.state.identity)


@app.get('/v1/auth/me')
def auth_me(request: Request,response: Response):
    from .auth import current
    identity=current(request,_write_dsn())
    response.headers['Cache-Control']='no-store'
    return {'user':{k:v for k,v in identity.items() if k not in {'csrf_token','_session_hash','_demo'}} if identity else None,
            'csrf_token':identity['csrf_token'] if identity else None}


@app.post('/v1/auth/login')
def auth_login(payload: LoginRequest,request: Request,response: Response):
    from .auth import login,COOKIE,SESSION_SECONDS
    token,user,csrf=login(request,payload.username,payload.password,_write_dsn())
    response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,samesite='strict',
                        secure=request.url.scheme=='https',path='/')
    response.headers['Cache-Control']='no-store'
    return {'user':user,'csrf_token':csrf}


@app.post('/v1/auth/logout')
def auth_logout(request: Request,response: Response):
    from .auth import logout,COOKIE
    logout(request,_write_dsn());response.delete_cookie(COOKIE,path='/')
    return {'status':'signed_out'}


@app.get('/v1/authority/candidates')
def authority_candidates(question: str = Query(min_length=1,max_length=2000)):
    from .authority import candidates
    try:return jsonable_encoder({'candidates':candidates(question,_read_dsn())})
    except ValueError as exc:raise HTTPException(422,detail={'error_code':str(exc)})


@app.get('/v1/authority/rules')
def authority_rules():
    from .authority import list_rules
    return jsonable_encoder({'rules':list_rules(_read_dsn())})


@app.post('/v1/authority/rules')
def authority_register(payload: AuthorityRequest,request: Request):
    from .authority import register
    try:rule=register(payload.model_dump(),request.state.identity,_write_dsn())
    except ValueError as exc:raise HTTPException(422,detail={'error_code':str(exc)})
    return jsonable_encoder({'rule':rule})


@app.get('/v1/authority/rules/{rule_id}/events')
def authority_history(rule_id: str):
    from .authority import history
    return jsonable_encoder({'events':history(rule_id,_read_dsn())})


@app.post('/v1/authority/rules/{rule_id}/events')
def authority_activate(rule_id: str,payload: AuthorityEventRequest,request: Request):
    from .authority import activation
    return jsonable_encoder({'event':activation(rule_id,payload.model_dump(),request.state.identity,_write_dsn())})
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
WEB = WEB_DIR / "index.html"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB)


@app.get("/health")
def health():
    try:
        with psycopg2.connect(_read_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.execute("SELECT count(*) FROM document_assets WHERE status='indexed' AND 'public'=ANY(acl) AND NOT needs_review")
            document_count = cur.fetchone()[0]
        return {"status": "ok", "database": "ready", "agent": "sql+rag",
                "capabilities": ["sql", "rag", "multifile_rag", "industrial_rules", "industrial_nl2sql", "fact_reviews", "local_identity", "fact_approvals", "source_authority", "cad_assets", "cad_dimensions", "file_jobs", "private_pdf_answer"],
                "knowledge": "ready" if document_count else "empty", "document_count": document_count}
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})


def _stored_config() -> dict:
    try:
        return load_model_config()
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})


def _config_source(stored: dict) -> str:
    if stored.get("api_key"):
        return "app_settings"
    return "environment" if os.environ.get("NEW_API_KEY") else "unset"


def _profiles_safe() -> dict:
    try:
        data = public_profiles()
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})
    data["protocols"] = [{"id": key, "label": label} for key, label in PROTOCOLS.items()]
    return jsonable_encoder(data)


def _profile_from_request(request: ProfileRequest) -> dict:
    """探活用的完整配置：指定配置 > 当前启用配置 > 环境变量 > 默认值，请求字段优先。"""
    try:
        state = list_profiles()
        stored = next((item for item in state["items"] if item.get("id") == request.profile_id), {})
        if not stored:
            stored = load_model_config()
    except psycopg2.Error:
        stored = {}
    profile = dict(stored)
    for field in ("protocol", "base_url", "model", "auth_style", "headers", "proxy",
                  "max_tokens", "timeout_s"):
        value = getattr(request, field, None)
        if value is not None:
            profile[field] = value
    profile["api_key"] = (request.api_key or "").strip() or stored.get("api_key", "")
    profile["base_url"] = profile.get("base_url") or os.environ.get("NEW_API_BASE_URL") or DEFAULT_BASE_URL
    profile["model"] = profile.get("model") or os.environ.get("NEW_API_MODEL") or DEFAULT_MODEL
    profile["api_key"] = profile.get("api_key") or os.environ.get("NEW_API_KEY") or ""
    return profile


@app.get("/v1/model/profiles")
def get_profiles():
    """已保存的接入配置列表（密钥只回显掩码）+ 可选协议。"""
    return _profiles_safe()


@app.get("/v1/model/profiles/export")
def export_profiles(include_keys: bool = Query(default=False)):
    state = list_profiles()
    items = []
    for item in state["items"]:
        payload = {field: item.get(field) for field in PROFILE_FIELDS}
        payload["id"] = item.get("id")
        if not include_keys:
            payload.pop("api_key", None)
        items.append(payload)
    return jsonable_encoder({"exported_at": datetime.now(timezone.utc).isoformat(),
                             "include_keys": include_keys, "active": state.get("active"),
                             "profiles": items})


@app.post("/v1/model/profiles/import")
def import_profiles(request: ProfileImportRequest):
    """导入配置（ccswitch 风格的搬迁）：同名不覆盖，一律作为新配置写入。"""
    imported = []
    for raw in request.profiles[:50]:
        if not isinstance(raw, dict):
            continue
        payload = {field: raw.get(field) for field in PROFILE_FIELDS if raw.get(field) is not None}
        payload["id"] = ""
        if not payload.get("name"):
            payload["name"] = "导入配置"
        imported.append(public_profile(save_profile(payload)))
    if request.activate_first and imported:
        activate_profile(imported[0]["id"])
    return jsonable_encoder({"imported": len(imported), **_profiles_safe()})


@app.post("/v1/model/profiles")
def create_profile(request: ProfileRequest):
    raw = request.model_dump(exclude_none=True)
    raw.pop("profile_id", None)
    raw["id"] = ""
    if not (raw.get("name") or "").strip():
        raise HTTPException(400, detail={"error_code": "PROFILE_NAME_REQUIRED"})
    if not (raw.get("api_key") or "").strip():
        raw.pop("api_key", None)
    return jsonable_encoder(public_profile(save_profile(raw)))


@app.put("/v1/model/profiles/{profile_id}")
def update_profile(profile_id: str, request: ProfileRequest):
    state = list_profiles()
    if not any(item.get("id") == profile_id for item in state["items"]):
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    raw = request.model_dump(exclude_none=True)
    raw.pop("profile_id", None)
    raw["id"] = profile_id
    if not (raw.get("api_key") or "").strip():
        raw.pop("api_key", None)      # 留空 = 保持原密钥
    return jsonable_encoder(public_profile(save_profile(raw)))


@app.delete("/v1/model/profiles/{profile_id}")
def remove_profile(profile_id: str):
    if not delete_profile(profile_id):
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    return _profiles_safe()


@app.post("/v1/model/profiles/{profile_id}/activate")
def activate(profile_id: str):
    try:
        activate_profile(profile_id)
    except KeyError:
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    return _profiles_safe()


@app.get("/v1/model/config")
def get_model_config():
    """当前启用配置（兼容单配置调用方）。"""
    stored = _stored_config()
    view = public_model_config(stored, source=_config_source(stored))
    view["environment_fallback"] = bool(os.environ.get("NEW_API_KEY"))
    return jsonable_encoder(view)


@app.put("/v1/model/config")
def put_model_config(request: ProfileRequest):
    stored = _stored_config()
    if request.api_key is None or not request.api_key.strip():
        api_key = stored.get("api_key", "")
    elif request.api_key.strip().lower() in {"clear", "none", "-"}:
        api_key = ""
    else:
        api_key = request.api_key.strip()
    base_url = normalize_base_url(request.base_url) or stored.get("base_url") or DEFAULT_BASE_URL
    model = (request.model or stored.get("model") or DEFAULT_MODEL).strip()
    save_model_config(base_url, model, api_key, protocol=request.protocol or stored.get("protocol") or OPENAI)
    return jsonable_encoder(public_model_config(active_profile(),
                                                source="app_settings" if api_key else "unset"))


@app.delete("/v1/model/config")
def delete_model_config():
    clear_model_config()
    return {"has_key": False, "key_masked": "", "source": "unset"}


@app.post("/v1/model/config/models")
def probe_models(request: ProfileRequest):
    profile = _profile_from_request(request)
    result = provider_list_models(profile)
    result["base_url"] = profile.get("base_url")
    result["protocol"] = profile.get("protocol") or OPENAI
    return jsonable_encoder(result)


@app.post("/v1/model/config/test")
def probe_chat(request: ProfileRequest):
    profile = _profile_from_request(request)
    result = provider_test_chat(profile)
    result["base_url"] = profile.get("base_url")
    result["configured_model"] = profile.get("model")
    return jsonable_encoder(result)

@app.post("/v1/chat")
def chat(request: ChatRequest):
    agent = DataMindAgent()
    try:
        return jsonable_encoder(agent.run(request.question,
            mode=request.mode, database_sources=request.scope.database_sources if request.scope else None,
            document_collections=request.scope.document_collections if request.scope else None,
            document_refs=[r.model_dump() for r in request.scope.document_refs] if request.scope and request.scope.document_refs is not None else None))
    finally:
        agent.close()


@app.post("/v1/diagnostics/sql")
def diagnostic_sql():
    """Fixed query proving browser → Agent → Guard → readonly DB without a model call."""
    question = "2024年一共有多少笔订单？"
    sql = ("SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' "
           "AND created_at < '2025-01-01'")
    agent = DataMindAgent()
    try:
        return jsonable_encoder(agent.run(question, database_sources=["demo_retail"],
                                         generator=lambda *_: {"sql": sql, "interpretation": "固定链路自检"}))
    finally:
        agent.close()


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    updates: queue.Queue = queue.Queue()
    agent = DataMindAgent(sink=updates.put)

    def worker():
        try:
            agent.run(request.question, mode=request.mode,
                      database_sources=request.scope.database_sources if request.scope else None,
                      document_collections=request.scope.document_collections if request.scope else None,
            document_refs=[r.model_dump() for r in request.scope.document_refs] if request.scope and request.scope.document_refs is not None else None)
        finally:
            agent.close()
            updates.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def events():
        while True:
            try:
                event = updates.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                break
            yield "event: trace\ndata: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _public_document(document_id: str) -> dict:
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT * FROM document_assets WHERE document_id=%s AND status='indexed'
                       AND 'public'=ANY(acl) AND NOT needs_review AND collection=ANY(%s)""",
                    (document_id, sorted(COLLECTIONS)))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, detail={"error_code": "DOCUMENT_NOT_FOUND"})
    return dict(row)


@app.get("/v1/documents")
def list_documents(collection: str | None = None):
    if collection and collection not in COLLECTIONS:
        raise HTTPException(400, detail={"error_code": "COLLECTION_NOT_ALLOWED"})
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT document_id,title,collection,file_type,origin,page_count,doc_version,sha256,
                              quality_score,quality_detail,parser_version FROM document_assets
                       WHERE status='indexed' AND 'public'=ANY(acl) AND NOT needs_review AND collection=ANY(%s)
                       ORDER BY title LIMIT 200""", ([collection] if collection else sorted(COLLECTIONS),))
        return {"documents": jsonable_encoder([dict(row) for row in cur.fetchall()])}


@app.get("/v1/documents/{document_id}")
def get_document(document_id: str):
    document = _public_document(document_id)
    document.pop("object_uri", None)
    return jsonable_encoder(document)


def _source_path(document: dict) -> Path:
    path = Path(document.get("object_uri") or "").resolve()
    if not any(path.is_relative_to(ROOT / name) for name in ("data/knowledge_base", "data/ohr_bench_samples", "data/industrial_demo")) or not path.is_file():
        raise HTTPException(404, detail={"error_code": "SOURCE_FILE_UNAVAILABLE"})
    if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
        raise HTTPException(409, detail={"error_code": "SOURCE_VERSION_MISMATCH"})
    return path


def _expected_document(document,version,sha):
    if version is not None or sha is not None:
        if version is None or sha is None:
            raise HTTPException(422,detail={'error_code':'DOCUMENT_SELECTION_INVALID'})
        if document['doc_version']!=version or document['sha256']!=sha:
            raise HTTPException(409,detail={'error_code':'DOCUMENT_SELECTION_CHANGED'})


@app.get("/v1/documents/{document_id}/file")
def document_file(document_id: str,expected_version: int | None=Query(default=None,ge=1),
                  expected_sha256: str | None=Query(default=None,pattern=r'^[a-f0-9]{64}$')):
    document = _public_document(document_id)
    _expected_document(document,expected_version,expected_sha256)
    path = _source_path(document)
    return FileResponse(path, media_type="application/pdf" if document["file_type"] == "pdf" else "application/json",
                        filename=path.name, content_disposition_type="inline")


@app.get("/v1/documents/{document_id}/facts")
def document_facts(document_id: str):
    from .field_store import read_verified_facts
    document = _public_document(document_id)
    source = _source_path(document)
    facts = read_verified_facts(document, source, _read_dsn())
    return {"document_id": document_id, "document_version": document["doc_version"], "source_sha256": document["sha256"],
            "parser_version": document["parser_version"], "facts": jsonable_encoder(facts), "fact_count": len(facts),
            "quality": (document.get("quality_detail") or {}).get("fact_extraction")}


@app.get('/v1/documents/{document_id}/facts/{fact_id}/reviews')
def get_fact_reviews(document_id: str,fact_id: str):
    document=_public_document(document_id);_source_path(document)
    from .fact_reviews import history
    return {'reviews':jsonable_encoder(history(document_id,fact_id,_read_dsn())),
            'semantics':'Authenticated local extraction review; legacy rows have no authenticated identity; separate from approval/authority'}


@app.post('/v1/documents/{document_id}/facts/{fact_id}/reviews')
def review_document_fact(document_id: str,fact_id: str,request: FactReviewRequest,http_request: Request):
    from .field_store import read_verified_facts
    from .fact_reviews import record
    document=_public_document(document_id);source=_source_path(document)
    facts=read_verified_facts(document,source,_read_dsn())
    fact=next((f for f in facts if f['fact_id']==fact_id),None)
    if not fact:raise HTTPException(409,detail={'error_code':'FACT_UNAVAILABLE_OR_STALE'})
    identity=http_request.state.identity
    if request.reviewer_label is not None and request.reviewer_label!=identity['display_name']:
        raise HTTPException(422,detail={'error_code':'REVIEWER_IDENTITY_SERVER_OWNED'})
    payload=request.model_dump();payload['reviewer_label']=identity['display_name'];payload['reason']=payload['reason'].strip()
    if not payload['reason']:raise HTTPException(422,detail={'error_code':'REVIEW_REASON_REQUIRED'})
    payload.update(reviewer_user_id=identity['user_id'],reviewer_roles=identity['roles'])
    result=record(document,fact,source,payload,_write_dsn(),identity=identity)
    current_fact=next((f for f in read_verified_facts(document,source,_read_dsn()) if f['fact_id']==fact_id),None)
    return {'review':jsonable_encoder({k:v for k,v in result.items() if k not in ('fact_snapshot','request_payload')}),
            'field_state':jsonable_encoder({k:current_fact[k] for k in ('latest_review','latest_approval','formal_approval_status')}) if current_fact else None,
            'semantics':'Only extraction fidelity is reviewed; source approval and unresolved conflicts remain unchanged'}


@app.get('/v1/documents/{document_id}/facts/{fact_id}/approvals')
def get_fact_approvals(document_id: str,fact_id: str):
    document=_public_document(document_id);_source_path(document)
    from .approvals import history
    return {'approvals':jsonable_encoder(history(document_id,fact_id,_read_dsn())),
            'semantics':'Separate version-bound local approval; does not overwrite source declarations or resolve conflicts'}


@app.post('/v1/documents/{document_id}/facts/{fact_id}/approvals')
def approve_document_fact(document_id: str,fact_id: str,request: FactApprovalRequest,http_request: Request):
    from .field_store import read_verified_facts
    from .approvals import record
    document=_public_document(document_id);source=_source_path(document)
    fact=next((f for f in read_verified_facts(document,source,_read_dsn()) if f['fact_id']==fact_id),None)
    if not fact:raise HTTPException(409,detail={'error_code':'FACT_UNAVAILABLE_OR_STALE'})
    payload=request.model_dump();payload['reason']=payload['reason'].strip()
    if not payload['reason']:raise HTTPException(422,detail={'error_code':'APPROVAL_REASON_REQUIRED'})
    identity=http_request.state.identity
    payload.update(approver_user_id=identity['user_id'],approver_label=identity['display_name'],approver_roles=identity['roles'])
    result=record(document,fact,source,payload,_write_dsn(),identity=identity)
    current_fact=next((f for f in read_verified_facts(document,source,_read_dsn()) if f['fact_id']==fact_id),None)
    return {'approval':jsonable_encoder({k:v for k,v in result.items() if k not in {'fact_snapshot','request_payload'}}),
            'field_state':jsonable_encoder({k:current_fact[k] for k in ('latest_review','latest_approval','formal_approval_status')}) if current_fact else None,
            'semantics':'A separate local approval; original approval declaration and conflicting source values are preserved'}


@app.get("/v1/documents/{document_id}/pages/{page_no}")
def document_page(document_id: str,page_no: int,expected_version: int | None=Query(default=None,ge=1),
                  expected_sha256: str | None=Query(default=None,pattern=r'^[a-f0-9]{64}$')):
    document = _public_document(document_id)
    _expected_document(document,expected_version,expected_sha256)
    if document["file_type"] != "pdf" or not 1 <= page_no <= (document["page_count"] or 0):
        raise HTTPException(404, detail={"error_code": "SOURCE_PAGE_UNAVAILABLE"})
    _source_path(document)
    image_root = ROOT / "data/object_store/page_images"
    path = (image_root / document_id / f"page_{page_no:03d}.png").resolve()
    if not path.is_relative_to(image_root) or not path.is_file():
        raise HTTPException(404, detail={"error_code": "SOURCE_PAGE_UNAVAILABLE"})
    return FileResponse(path, media_type="image/png")


def _run_exists(cur, run_id: str):
    cur.execute("SELECT run_id FROM runs WHERE run_id=%s", (run_id,))
    if not cur.fetchone():
        raise HTTPException(404, detail={"error_code": "RUN_NOT_FOUND"})


@app.get("/v1/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=200)):
    """Recent runs for the 「运行记录」 view."""
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT run_id, question, status, confidence_level, started_at, ended_at
                       FROM runs ORDER BY started_at DESC LIMIT %s""", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    return {"runs": jsonable_encoder(rows)}


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str):
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, detail={"error_code": "RUN_NOT_FOUND"})
    return jsonable_encoder(dict(row))


@app.get("/v1/runs/{run_id}/events")
def get_events(run_id: str, after: str | None = Query(default=None)):
    if after and not after.startswith("evt-" + run_id[4:] + "-"):
        raise HTTPException(400, detail={"error_code": "INVALID_CURSOR"})
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _run_exists(cur, run_id)
        cur.execute("""SELECT * FROM trace_events WHERE run_id=%s AND event_id > %s
                       ORDER BY event_id LIMIT 200""", (run_id, after or ""))
        rows = [dict(r) for r in cur.fetchall()]
    return {"events": jsonable_encoder(rows), "next_cursor": rows[-1]["event_id"] if rows else after}


@app.get("/v1/runs/{run_id}/evidence")
def get_evidence(run_id: str):
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _run_exists(cur, run_id)
        cur.execute("SELECT * FROM evidence_nodes WHERE run_id=%s ORDER BY created_at, evidence_id", (run_id,))
        nodes = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM evidence_edges WHERE run_id=%s ORDER BY edge_id", (run_id,))
        edges = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM claims WHERE run_id=%s ORDER BY created_at", (run_id,))
        claims = [dict(r) for r in cur.fetchall()]
    return jsonable_encoder({"nodes": nodes, "edges": edges, "claims": claims})


@app.get('/v1/runs/{run_id}/conflicts')
def get_conflicts(run_id: str):
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _run_exists(cur,run_id)
        cur.execute('SELECT payload FROM conflict_records WHERE run_id=%s ORDER BY conflict_id',(run_id,))
        return {'conflicts':[r['payload'] for r in cur.fetchall()]}
