<script setup lang="ts">
import { useRouter } from 'vue-router'
import StarField from '../components/StarField.vue'
import FlipCard from '../components/FlipCard.vue'
import ConstellationGraph from '../components/stargraph/ConstellationGraph.vue'
import { useReveal } from '../composables/useReveal'
import type { EvidenceNode, EvidenceEdge } from '../api/types'

const router = useRouter()
useReveal()

const features = [
  {
    icon: '◉',
    title: '可解释',
    desc: '答案不是终点。从自然语言到 SQL、检索、验证的完整推理链路，全程可回放。',
  },
  {
    icon: '⌖',
    title: '可溯源',
    desc: '每条证据都定位到原文页码、数据库行、字段。点开星点，回到它诞生的地方。',
  },
  {
    icon: '✦',
    title: '跨源多跳',
    desc: '结构化数据库与非结构化文档自动路由、联合核验，冲突来源并陈、保留双方原值。',
  },
]

const steps = [
  { n: '01', t: '理解问题', d: '解析意图，判断该查库、查文档，还是两者联合' },
  { n: '02', t: '路由与规划', d: '选择数据源，生成带预算的执行计划' },
  { n: '03', t: '执行与验证', d: '生成 SQL / 检索片段，逐条校验数字与引用' },
  { n: '04', t: '答案 + 证据', d: '输出可点击的证据图，冲突如实呈现' },
]

// 落地页静态演示用的证据星图（非交互，仅展示）
const sampleNodes: EvidenceNode[] = [
  { evidence_id: 'n-claim', run_id: 'demo', node_type: 'Claim', ref_key: 'claim-1', payload: { text: '答案主张' } },
  { evidence_id: 'n-verify', run_id: 'demo', node_type: 'VerificationCheck', ref_key: 'v-1', payload: { measurement_id: 'M-1', compliance: 'ok' } },
  { evidence_id: 'n-row', run_id: 'demo', node_type: 'SQLRow', ref_key: 'row-1', payload: { columns: ['产品', '销售额'], rows: [['产品 A', '¥82.1万']] } },
  { evidence_id: 'n-chunk', run_id: 'demo', node_type: 'Chunk', ref_key: 'chunk-1', payload: { text: '……' } },
  { evidence_id: 'n-sql', run_id: 'demo', node_type: 'SQLQuery', ref_key: 'sql-1', payload: { sql: 'SELECT … ORDER BY … LIMIT 3' } },
  { evidence_id: 'n-doc', run_id: 'demo', node_type: 'Document', ref_key: 'doc-1', payload: { title: '产品分析报告' } },
  { evidence_id: 'n-conflict', run_id: 'demo', node_type: 'ConflictRecord', ref_key: 'conf-1', payload: { conflict_type: 'value_conflict' } },
]
const sampleEdges: EvidenceEdge[] = [
  { src_evidence_id: 'n-verify', dst_evidence_id: 'n-row', edge_type: 'derived_from' },
  { src_evidence_id: 'n-row', dst_evidence_id: 'n-sql', edge_type: 'derived_from' },
  { src_evidence_id: 'n-claim', dst_evidence_id: 'n-verify', edge_type: 'supports' },
  { src_evidence_id: 'n-claim', dst_evidence_id: 'n-chunk', edge_type: 'supports' },
  { src_evidence_id: 'n-chunk', dst_evidence_id: 'n-doc', edge_type: 'retrieved_from' },
  { src_evidence_id: 'n-conflict', dst_evidence_id: 'n-verify', edge_type: 'derived_from' },
]
</script>

<template>
  <div class="landing">
    <!-- 导航 -->
    <header class="nav">
      <div class="brand">
        <span class="logo">DM</span>
        <strong>DataMind Agent</strong>
      </div>
      <nav class="links">
        <a href="#principle">原理</a>
        <a href="#features">能力</a>
        <a href="#starmap">星图</a>
        <a href="#how">流程</a>
      </nav>
      <button class="cta" @click="router.push('/app')">进入体验</button>
    </header>

    <!-- Hero -->
    <section id="top" class="hero">
      <StarField :density="2.2" />
      <div class="hero-inner">
        <div class="pill">多模态数据驱动 · 可解释 · 可溯源</div>
        <h1>把 AI 的黑箱，<br />拆成一张<span class="gradient-text glow-text">看得见的星图</span></h1>
        <p class="sub">
          DataMind Agent —— 面向数据库与知识库的精准问数智能体。<br />
          每一句答案，都能回溯到它的 SQL、检索片段与原始数据。
        </p>
        <div class="hero-cta">
          <button class="primary" @click="router.push('/app')">点击即试 →</button>
          <a class="ghost" href="#principle">了解原理</a>
        </div>
      </div>
      <div class="scroll-hint">↓ 往下看</div>
    </section>

    <!-- 原理：翻牌卡 -->
    <section id="principle" class="section">
      <div class="sec-head reveal">
        <div class="eyebrow">Why DataMind</div>
        <h2>AI 黑箱？</h2>
        <p>翻过来，看看我们。</p>
      </div>

      <div class="flip-wrap reveal">
        <FlipCard auto-flip class="flipcard">
          <template #front>
            <div class="face-content">
              <div class="big">AI 黑箱？</div>
              <p>一句话进去，一个答案出来——<br />中间发生了什么，没人知道。</p>
              <span class="hint">点击翻转</span>
            </div>
          </template>
          <template #back>
            <div class="face-content">
              <div class="big light">看看我们</div>
              <ul class="checks">
                <li>✓ SQL 全文可见</li>
                <li>✓ 检索片段可点回原文</li>
                <li>✓ 冲突来源并陈</li>
                <li>✓ 推理链路可回放</li>
              </ul>
            </div>
          </template>
        </FlipCard>

        <div class="principle-text reveal">
          <h3>答案，必须经得起追问</h3>
          <p>
            传统大模型问答像一张不透明的底片；DataMind 把它显影成一幅星图：SQL、检索、核验、冲突，
            每一步都摆上台面，每一条证据都可点击、可回溯。
          </p>
          <a class="link" href="#starmap">看看星图长什么样 ↓</a>
        </div>
      </div>
    </section>

    <!-- 能力 -->
    <section id="features" class="section">
      <div class="sec-head reveal">
        <div class="eyebrow">Capabilities</div>
        <h2>三个关键词</h2>
      </div>
      <div class="feat-grid">
        <div v-for="(f, i) in features" :key="f.title" class="feat glass reveal" :style="{ transitionDelay: i * 90 + 'ms' }">
          <div class="feat-icon">{{ f.icon }}</div>
          <h3>{{ f.title }}</h3>
          <p>{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- 星图 -->
    <section id="starmap" class="section">
      <div class="sec-head reveal">
        <div class="eyebrow">Evidence Constellation</div>
        <h2>每一颗星，都是一条证据</h2>
        <p>答案是中心的恒星，证据是环绕的星点，连线是推导关系。</p>
      </div>
      <div class="starmap glass reveal">
        <ConstellationGraph :nodes="sampleNodes" :edges="sampleEdges" :interactive="false" :width="700" :height="480" />
      </div>
      <div class="legend reveal">
        <span><i style="background:#fcd34d"></i>答案主张</span>
        <span><i style="background:#38bdf8"></i>SQL</span>
        <span><i style="background:#818cf8"></i>检索片段</span>
        <span><i style="background:#a78bfa"></i>文档</span>
        <span><i style="background:#4ade80"></i>核验</span>
        <span><i style="background:#fb7185"></i>冲突</span>
      </div>
    </section>

    <!-- 流程 -->
    <section id="how" class="section">
      <div class="sec-head reveal">
        <div class="eyebrow">How it works</div>
        <h2>一条问题，四步落地</h2>
      </div>
      <div class="steps">
        <div v-for="(s, i) in steps" :key="s.n" class="step reveal" :style="{ transitionDelay: i * 90 + 'ms' }">
          <div class="step-n">{{ s.n }}</div>
          <h3>{{ s.t }}</h3>
          <p>{{ s.d }}</p>
        </div>
      </div>
    </section>

    <!-- 最终 CTA -->
    <section id="try" class="final">
      <StarField :density="1.6" />
      <div class="final-inner reveal">
        <h2>心动了？<span class="gradient-text">点击即试</span></h2>
        <p>现在就问一句，看证据如何点亮。</p>
        <button class="primary big" @click="router.push('/app')">开始体验 →</button>
      </div>
    </section>

    <footer class="foot">
      <span>DataMind Agent · 赛题八 多模态数据驱动的可解释精准问数/问答智能体</span>
      <span>演示数据，不代表真实生产环境</span>
    </footer>
  </div>
</template>

<style scoped>
.landing {
  background:
    radial-gradient(1400px 800px at 80% -10%, rgba(56, 189, 248, 0.1), transparent 55%),
    radial-gradient(1200px 700px at 10% 30%, rgba(129, 140, 248, 0.08), transparent 55%),
    var(--color-void);
  color: #e5e9f0;
  overflow-x: hidden;
}
.nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 16px 40px;
  background: rgba(5, 7, 13, 0.55);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--color-line);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-weight: 700;
  color: #04101d;
  background: linear-gradient(135deg, #7dd3fc, #818cf8);
}
.brand strong {
  font-size: 15px;
  font-weight: 600;
}
.links {
  display: flex;
  gap: 22px;
  margin-left: auto;
}
.links a {
  color: #94a3b8;
  text-decoration: none;
  font-size: 14px;
  transition: color 0.15s;
}
.links a:hover {
  color: #e5e9f0;
}
.cta {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--color-line);
  color: #e5e9f0;
  border-radius: 999px;
  padding: 8px 18px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.cta:hover {
  background: rgba(56, 189, 248, 0.16);
  border-color: rgba(56, 189, 248, 0.4);
}

.hero {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 120px 24px 80px;
}
.hero-inner {
  position: relative;
  text-align: center;
  max-width: 860px;
  animation: fadeup 1.1s cubic-bezier(0.16, 1, 0.3, 1) both;
}
.pill {
  display: inline-block;
  font-size: 12.5px;
  color: #a5b4fc;
  border: 1px solid rgba(129, 140, 248, 0.3);
  background: rgba(129, 140, 248, 0.08);
  border-radius: 999px;
  padding: 6px 16px;
  margin-bottom: 26px;
}
.hero h1 {
  font-size: clamp(34px, 6vw, 64px);
  line-height: 1.15;
  font-weight: 700;
  margin: 0;
  letter-spacing: -0.02em;
}
.sub {
  margin-top: 22px;
  font-size: clamp(15px, 2vw, 18px);
  line-height: 1.8;
  color: #94a3b8;
}
.hero-cta {
  margin-top: 34px;
  display: flex;
  gap: 14px;
  justify-content: center;
}
.primary {
  background: linear-gradient(135deg, #38bdf8, #6366f1);
  color: #04101d;
  border: none;
  border-radius: 999px;
  padding: 13px 30px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(56, 189, 248, 0.3);
}
.primary.big {
  padding: 15px 38px;
  font-size: 17px;
}
.ghost {
  display: inline-flex;
  align-items: center;
  color: #cbd5e1;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 13px 28px;
  font-size: 16px;
  text-decoration: none;
  transition: all 0.15s;
}
.ghost:hover {
  border-color: rgba(148, 163, 184, 0.4);
  background: rgba(255, 255, 255, 0.04);
}
.scroll-hint {
  position: absolute;
  bottom: 26px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 12px;
  color: #475569;
  animation: floaty 2.4s ease-in-out infinite;
}

.section {
  max-width: 1100px;
  margin: 0 auto;
  padding: 96px 24px;
}
.sec-head {
  text-align: center;
  margin-bottom: 48px;
}
.eyebrow {
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #64748b;
}
.sec-head h2 {
  font-size: clamp(26px, 4vw, 40px);
  font-weight: 700;
  margin: 10px 0 8px;
}
.sec-head p {
  color: #94a3b8;
  font-size: 16px;
  line-height: 1.7;
  margin: 0;
}

.flip-wrap {
  display: grid;
  grid-template-columns: minmax(0, 420px) 1fr;
  gap: 48px;
  align-items: center;
  justify-items: center;
}
.flipcard {
  width: 100%;
  max-width: 380px;
  height: 300px;
}
.face-content {
  padding: 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
.face-content .big {
  font-size: 34px;
  font-weight: 700;
  color: #e5e9f0;
}
.face-content .big.light {
  color: #e0f2fe;
}
.face-content p {
  color: #94a3b8;
  font-size: 14px;
  line-height: 1.7;
  margin: 0;
}
.hint {
  font-size: 12px;
  color: #475569;
}
.checks {
  list-style: none;
  margin: 0;
  padding: 0;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 15px;
  color: #dbe4f0;
}
.principle-text h3 {
  font-size: 22px;
  margin: 0 0 14px;
}
.principle-text p {
  color: #94a3b8;
  font-size: 15.5px;
  line-height: 1.9;
  margin: 0 0 20px;
}
.link {
  color: #7dd3fc;
  text-decoration: none;
  font-size: 14px;
}

.feat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}
.feat {
  border-radius: 20px;
  padding: 28px;
}
.feat-icon {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 20px;
  color: #04101d;
  background: linear-gradient(135deg, #7dd3fc, #818cf8);
  margin-bottom: 18px;
}
.feat h3 {
  font-size: 19px;
  margin: 0 0 10px;
}
.feat p {
  color: #94a3b8;
  font-size: 14px;
  line-height: 1.8;
  margin: 0;
}

.starmap {
  border-radius: 24px;
  padding: 20px;
  max-width: 760px;
  margin: 0 auto;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 18px;
  margin-top: 26px;
  font-size: 13px;
  color: #94a3b8;
}
.legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.legend i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 8px currentColor;
}

.steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}
.step {
  border-radius: 18px;
  padding: 24px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--color-line);
}
.step-n {
  font-size: 13px;
  font-weight: 700;
  color: #7dd3fc;
  margin-bottom: 12px;
}
.step h3 {
  font-size: 16px;
  margin: 0 0 8px;
}
.step p {
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.7;
  margin: 0;
}

.final {
  position: relative;
  padding: 130px 24px;
  text-align: center;
}
.final-inner {
  position: relative;
}
.final h2 {
  font-size: clamp(28px, 5vw, 46px);
  margin: 0 0 16px;
}
.final p {
  color: #94a3b8;
  font-size: 16px;
  margin: 0 0 34px;
}

.foot {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  padding: 30px 24px 44px;
  color: #475569;
  font-size: 12px;
  border-top: 1px solid var(--color-line);
}

@media (max-width: 900px) {
  .links {
    display: none;
  }
  .flip-wrap {
    grid-template-columns: 1fr;
    gap: 32px;
  }
  .feat-grid {
    grid-template-columns: 1fr;
  }
  .steps {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
