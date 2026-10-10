// 演示模式：后端未连接时，用与真实接口完全同构的数据点亮聊天 + 星链图。
// 数据结构与 /v1/runs/{id}/evidence 的 nodes/edges/claims 一致，
// 与 SSE 的 TraceEvent、FinalResult 一致。等后端就绪后，关掉演示模式即走真接口。

import type { TraceEvent, FinalResult, EvidenceGraph } from './types'

export interface DemoScenario {
  thinking: TraceEvent[]
  final: FinalResult
  graph: EvidenceGraph
}

const RUN = 'run-demo'

function ev(
  seq: number,
  event_type: string,
  agent_name: string,
  status: TraceEvent['status'],
  output_summary: Record<string, any> = {},
  tool_name: string | null = null,
): TraceEvent {
  return {
    event_id: `evt-demo-${String(seq).padStart(4, '0')}`,
    run_id: RUN,
    event_type,
    agent_name,
    status,
    started_at: new Date().toISOString(),
    input_summary: {},
    output_summary,
    error_code: null,
    tool_name,
  }
}

function node(
  evidence_id: string,
  node_type: EvidenceGraph['nodes'][number]['node_type'],
  ref_key: string,
  payload: Record<string, any>,
): EvidenceGraph['nodes'][number] {
  return { evidence_id, run_id: RUN, node_type, ref_key, payload }
}

function edge(
  src_evidence_id: string,
  dst_evidence_id: string,
  edge_type: 'derived_from' | 'supports' | 'retrieved_from' | 'resolved_by',
): EvidenceGraph['edges'][number] {
  return { run_id: RUN, src_evidence_id, dst_evidence_id, edge_type }
}

// ── 场景一：工业温度核验（项目主打，含冲突 + 权威规则，星图最丰富）──────────
function industrialDemo(): DemoScenario {
  const answer =
    '已核验 2026 年 9 月 A102 工序 30 的温度记录：批次 B-102-0918 的实测温度 47.3℃，' +
    '超过上限 45.0℃，判定超限。但质检报告记录该批次为 46.8℃（判定合格），' +
    '两处来源数值冲突。按来源权威规则，生产记录精确测量值优先，报告值保留并标注差异。'

  const thinking: TraceEvent[] = [
    ev(1, 'run.started', 'orchestrator', 'succeeded', { question_length: 34 }),
    ev(2, 'query.parsed', 'query_understanding', 'succeeded', { route: 'industrial' }),
    ev(3, 'plan.created', 'planner', 'succeeded', { plan_id: 'plan-demo-01' }),
    ev(4, 'sql.executed', 'sql_agent', 'succeeded', { row_count: 1, elapsed_ms: 42 }, 'postgresql'),
    ev(5, 'retrieval.completed', 'knowledge_agent', 'succeeded', { chunk_count: 1 }, 'knowledge.retrieve'),
    ev(6, 'verification.failed', 'industrial_agent', 'warning', { conflict: true }, 'industrial.compare'),
    ev(7, 'evidence.attached', 'industrial_agent', 'succeeded', { node_count: 9 }),
    ev(8, 'answer.completed', 'orchestrator', 'succeeded', { confidence_level: 'medium' }),
  ]

  const final: FinalResult = {
    run_id: RUN,
    status: 'COMPLETED',
    answer,
    confidence_level: 'medium',
    error_code: null,
    sql_result: {
      sql: "SELECT batch_id, temp_c, upper_limit, measured_at\nFROM demo_industrial.production_batches\nWHERE operation_no = 'A102-30'\n  AND measured_at >= '2026-09-01' AND measured_at < '2026-10-01'\n  AND temp_c > upper_limit\nORDER BY measured_at",
      columns: ['batch_id', 'temp_c', 'upper_limit', 'measured_at'],
      rows: [['B-102-0918', 47.3, 45.0, '2026-09-18 14:20']],
      row_count: 1,
      elapsed_ms: 42,
      source_id: 'demo_industrial',
    },
    rag_result: {
      citations: [
        {
          document_id: 'q-202609-b',
          title: '质检报告_202609_B.pdf',
          text: '……批次 B-102-0918 实测温度 46.8℃，判定合格……',
          page_no: 3,
        },
      ],
    },
  }

  const graph: EvidenceGraph = {
    nodes: [
      node('ev-demo-claim', 'Claim', 'claim-demo', { text: answer }),
      node('ev-demo-vc', 'VerificationCheck', 'B-102-0918', {
        measurement_id: 'M-B1020918',
        batch_id: 'B-102-0918',
        operation_no: 'A102-30',
        metric: 'temperature',
        value: 47.3,
        unit: '℃',
        measured_at: '2026-09-18 14:20',
        compliance: 'conflict',
        agreement: false,
      }),
      node('ev-demo-sql', 'SQLQuery', 'sql-demo', {
        sql: "SELECT batch_id, temp_c, upper_limit FROM demo_industrial.production_batches WHERE batch_id = 'B-102-0918'",
        source_id: 'demo_industrial',
        prompt_version: 'industrial-sql-v1',
        sql_hash: '1a2b3c4d',
      }),
      node('ev-demo-row', 'SQLRow', 'row-demo', {
        columns: ['batch_id', 'temp_c', 'upper_limit', 'measured_at'],
        rows: [['B-102-0918', 47.3, 45.0, '2026-09-18 14:20']],
        row_count: 1,
      }),
      node('ev-demo-doc', 'Document', 'q-202609-b', {
        title: '质检报告_202609_B.pdf',
        document_id: 'q-202609-b',
        document_version: 3,
      }),
      node('ev-demo-chunk', 'Chunk', 'chunk-demo', {
        text: '……批次 B-102-0918 实测温度 46.8℃，判定合格……',
        title: '质检报告_202609_B.pdf',
        page_no: 3,
        used_in_answer: true,
      }),
      node('ev-demo-fact', 'DocumentFact', 'fact-demo', {
        field: 'temp_c',
        value: 46.8,
        unit: '℃',
        page_no: 3,
      }),
      node('ev-demo-conflict', 'ConflictRecord', 'conflict-demo', {
        conflict_type: 'value_conflict',
        metric: 'temperature',
        resolution_status: 'reported',
        normalized_values: {
          production_record: { value: 47.3, unit: '℃' },
          quality_report: { value: 46.8, unit: '℃' },
        },
        reason: '生产记录与质检报告对同一批次的温度记录不一致',
      }),
      node('ev-demo-rule', 'AuthorityRule', 'rule-demo', {
        rule_id: 'rule-precise-measurement',
        rule_version: 1,
      }),
    ],
    edges: [
      edge('ev-demo-claim', 'ev-demo-vc', 'supports'),
      edge('ev-demo-vc', 'ev-demo-row', 'derived_from'),
      edge('ev-demo-row', 'ev-demo-sql', 'derived_from'),
      edge('ev-demo-vc', 'ev-demo-fact', 'derived_from'),
      edge('ev-demo-fact', 'ev-demo-chunk', 'derived_from'),
      edge('ev-demo-chunk', 'ev-demo-doc', 'retrieved_from'),
      edge('ev-demo-conflict', 'ev-demo-vc', 'derived_from'),
      edge('ev-demo-vc', 'ev-demo-rule', 'resolved_by'),
    ],
    claims: [
      {
        claim_id: 'claim-demo',
        run_id: RUN,
        text: answer,
        status: 'verified',
        evidence_ids: ['ev-demo-sql', 'ev-demo-row', 'ev-demo-fact'],
        confidence_level: 'medium',
        verification: { conflict: true },
      },
    ],
  }

  return { thinking, final, graph }
}

// ── 场景二：零售问数（SQL，简洁链路）──────────────────────────────────────
function sqlDemo(): DemoScenario {
  const answer = '2025 年销售额最高的前三个产品是：无线降噪耳机 Pro（¥128.4 万）、智能手环 6（¥96.7 万）、便携蓝牙音箱（¥88.2 万）。'

  const thinking: TraceEvent[] = [
    ev(1, 'run.started', 'orchestrator', 'succeeded'),
    ev(2, 'query.parsed', 'query_understanding', 'succeeded', { route: 'sql' }),
    ev(3, 'plan.created', 'planner', 'succeeded', { plan_id: 'plan-demo-02' }),
    ev(4, 'sql.validated', 'sql_guard', 'succeeded', { tables: ['orders', 'order_items', 'products'] }),
    ev(5, 'sql.executed', 'sql_agent', 'succeeded', { row_count: 3, elapsed_ms: 28 }, 'postgresql'),
    ev(6, 'verification.passed', 'verification_agent', 'succeeded', { verified: true }),
    ev(7, 'evidence.attached', 'sql_agent', 'succeeded', { node_count: 4 }),
    ev(8, 'answer.completed', 'orchestrator', 'succeeded', { confidence_level: 'high' }),
  ]

  const final: FinalResult = {
    run_id: RUN,
    status: 'COMPLETED',
    answer,
    confidence_level: 'high',
    error_code: null,
    sql_result: {
      sql: "SELECT p.product_name, SUM(oi.quantity * oi.unit_price) AS sales_amount\nFROM order_items oi\nJOIN products p ON p.product_id = oi.product_id\nJOIN orders o ON o.order_id = oi.order_id\nWHERE o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'\nGROUP BY p.product_name\nORDER BY sales_amount DESC\nLIMIT 3",
      columns: ['product_name', 'sales_amount'],
      rows: [
        ['无线降噪耳机 Pro', 1284000],
        ['智能手环 6', 967000],
        ['便携蓝牙音箱', 882000],
      ],
      row_count: 3,
      elapsed_ms: 28,
      source_id: 'demo_retail',
    },
  }

  const graph: EvidenceGraph = {
    nodes: [
      node('ev-demo-claim', 'Claim', 'claim-demo', { text: answer }),
      node('ev-demo-vc', 'VerificationCheck', 'sql-verify', {
        compliance: 'ok',
        agreement: true,
      }),
      node('ev-demo-sql', 'SQLQuery', 'sql-demo', {
        sql: "SELECT p.product_name, SUM(oi.quantity * oi.unit_price) AS sales_amount FROM order_items oi JOIN products p ON p.product_id = oi.product_id GROUP BY p.product_name ORDER BY sales_amount DESC LIMIT 3",
        source_id: 'demo_retail',
        prompt_version: 'retail-sql-v1',
      }),
      node('ev-demo-row', 'SQLRow', 'row-demo', {
        columns: ['product_name', 'sales_amount'],
        rows: [
          ['无线降噪耳机 Pro', 1284000],
          ['智能手环 6', 967000],
          ['便携蓝牙音箱', 882000],
        ],
        row_count: 3,
      }),
    ],
    edges: [
      edge('ev-demo-claim', 'ev-demo-vc', 'supports'),
      edge('ev-demo-vc', 'ev-demo-row', 'derived_from'),
      edge('ev-demo-row', 'ev-demo-sql', 'derived_from'),
    ],
    claims: [
      {
        claim_id: 'claim-demo',
        run_id: RUN,
        text: answer,
        status: 'verified',
        evidence_ids: ['ev-demo-sql', 'ev-demo-row'],
        confidence_level: 'high',
        verification: {},
      },
    ],
  }

  return { thinking, final, graph }
}

// ── 场景三：文档问答（RAG，检索片段 → 原文）──────────────────────────────
function ragDemo(): DemoScenario {
  const answer =
    '七天无理由退货的条件是：自签收次日起 7 日内、商品未使用且包装附件齐全，即可申请无理由退货；' +
    '定制商品、已拆封的数码耗材等特殊品类不适用。'

  const thinking: TraceEvent[] = [
    ev(1, 'run.started', 'orchestrator', 'succeeded'),
    ev(2, 'query.parsed', 'query_understanding', 'succeeded', { route: 'rag' }),
    ev(3, 'plan.created', 'planner', 'succeeded', { plan_id: 'plan-demo-03' }),
    ev(4, 'retrieval.completed', 'knowledge_agent', 'succeeded', { chunk_count: 2 }, 'knowledge.retrieve'),
    ev(5, 'verification.passed', 'verification_agent', 'succeeded', { verified: true }),
    ev(6, 'evidence.attached', 'knowledge_agent', 'succeeded', { node_count: 4 }),
    ev(7, 'answer.completed', 'orchestrator', 'succeeded', { confidence_level: 'high' }),
  ]

  const final: FinalResult = {
    run_id: RUN,
    status: 'COMPLETED',
    answer,
    confidence_level: 'high',
    error_code: null,
    rag_result: {
      citations: [
        {
          document_id: 'kb-aftersale-04',
          title: '售后服务与退款管理制度.pdf',
          text: '自签收次日起 7 日内，商品未使用且包装、附件齐全的，可申请无理由退货。',
          page_no: 2,
        },
        {
          document_id: 'kb-aftersale-04',
          title: '售后服务与退款管理制度.pdf',
          text: '定制类商品、已拆封的数码耗材等不适用无理由退货。',
          page_no: 2,
        },
      ],
    },
  }

  const graph: EvidenceGraph = {
    nodes: [
      node('ev-demo-claim', 'Claim', 'claim-demo', { text: answer }),
      node('ev-demo-chunk1', 'Chunk', 'chunk-1', {
        text: '自签收次日起 7 日内，商品未使用且包装、附件齐全的，可申请无理由退货。',
        title: '售后服务与退款管理制度.pdf',
        page_no: 2,
        used_in_answer: true,
      }),
      node('ev-demo-chunk2', 'Chunk', 'chunk-2', {
        text: '定制类商品、已拆封的数码耗材等不适用无理由退货。',
        title: '售后服务与退款管理制度.pdf',
        page_no: 2,
        used_in_answer: true,
      }),
      node('ev-demo-doc', 'Document', 'kb-aftersale-04', {
        title: '售后服务与退款管理制度.pdf',
        document_id: 'kb-aftersale-04',
        document_version: 1,
      }),
    ],
    edges: [
      edge('ev-demo-claim', 'ev-demo-chunk1', 'supports'),
      edge('ev-demo-claim', 'ev-demo-chunk2', 'supports'),
      edge('ev-demo-chunk1', 'ev-demo-doc', 'retrieved_from'),
      edge('ev-demo-chunk2', 'ev-demo-doc', 'retrieved_from'),
    ],
    claims: [
      {
        claim_id: 'claim-demo',
        run_id: RUN,
        text: answer,
        status: 'verified',
        evidence_ids: ['ev-demo-chunk1', 'ev-demo-chunk2'],
        confidence_level: 'high',
        verification: {},
      },
    ],
  }

  return { thinking, final, graph }
}

export function demoScenario(mode: string): DemoScenario {
  if (mode === 'rag') return ragDemo()
  if (mode === 'retail' || mode === 'chinook') return sqlDemo()
  if (mode === 'industrial' || mode === 'industrial_sql') return industrialDemo()
  return industrialDemo()
}
