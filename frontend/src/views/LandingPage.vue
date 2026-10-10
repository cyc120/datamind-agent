<script setup lang="ts">
import { useRouter } from 'vue-router'
import WarpText from '../components/bits/WarpText/WarpText.vue'
import FoldText from '../components/bits/FoldText/FoldText.vue'
import BlurText from '../components/bits/BlurText/BlurText.vue'
import FallingText from '../components/bits/FallingText/FallingText.vue'
import RotatingText from '../components/bits/RotatingText/RotatingText.vue'
import TrueFocus from '../components/bits/TrueFocus/TrueFocus.vue'
import PixelSwap from '../components/bits/PixelSwap/PixelSwap.vue'
import FlipCard from '../components/FlipCard.vue'
import ConstellationGraph from '../components/stargraph/ConstellationGraph.vue'
import { useReveal } from '../composables/useReveal'
import type { EvidenceNode, EvidenceEdge } from '../api/types'

const router = useRouter()
useReveal()

/** Hero 副标题里轮换的能力词。 */
const rotating = ['精准问数', '文档问答', '跨源核验']

/** 关键词条：TrueFocus 会逐个聚焦。 */
const keywords = '可解释 可溯源 跨源核验 图文对照 冲突并陈'

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
      <div class="hero-inner">
        <div class="pill">多模态数据驱动 · 可解释 · 可溯源</div>

        <!-- 曲速文本：大标题 -->
        <div class="warp-slot">
          <WarpText text="把黑箱拆成星图" color="#0b1220" :font-size="'clamp(2.6rem, 8vw, 6.5rem)'"
                    :font-weight="800" :letter-spacing="'-0.045em'" :speed="0.5" :warp-strength="0.06" />
        </div>
        <h1 class="sr-only">把 AI 的黑箱，拆成一张看得见的星图</h1>

        <!-- 轮换文本：关键词 -->
        <p class="sub">
          DataMind Agent —— 面向数据库与知识库的
          <RotatingText :texts="rotating" class="rotator overflow-hidden" :rotation-interval="2200"
                        :transition="{ type: 'spring', damping: 26, stiffness: 320 }" />
          智能体。<br />
          每一句答案，都能回溯到它的 SQL、检索片段与原始数据。
        </p>

        <div class="hero-cta">
          <button class="primary" @click="router.push('/app')">点击即试 →</button>
          <a class="ghost" href="#principle">了解原理</a>
        </div>
      </div>
    </section>

    <!-- 关键词条：真正的专注 -->
    <section class="keyword-strip">
      <TrueFocus :sentence="keywords" :blur-amount="4" border-color="#2563eb"
                  glow-color="rgba(37, 99, 235, 0.35)" :animation-duration="0.45"
                  :pause-between-animations="0.9" />
    </section>

    <!-- 原理：翻牌卡 + 折叠文字 -->
    <section id="principle" class="section">
      <div class="sec-head reveal">
        <div class="eyebrow">Why DataMind</div>
        <FoldText text="答案要经得起追问" split-by="word" hinge="top" font-size="clamp(1.8rem, 4vw, 3rem)"
                  font-weight="800" color="#0b1220" :duration="0.6" :stagger="0.06" trigger="scroll" />
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

    <!-- 能力：模糊文字 + 三卡 -->
    <section id="features" class="section soft">
      <div class="sec-head">
        <div class="eyebrow">Capabilities</div>
        <BlurText text="三个关键词" :delay="120" animate-by="words" direction="top"
                  class="blur-heading" />
      </div>
      <div class="feat-grid">
        <div v-for="(f, i) in features" :key="f.title" class="feat glass reveal" :style="{ transitionDelay: i * 90 + 'ms' }">
          <div class="feat-icon">{{ f.icon }}</div>
          <h3>{{ f.title }}</h3>
          <p>{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- 星图：落差文字做小字说明 -->
    <section id="starmap" class="section">
      <div class="sec-head">
        <div class="eyebrow">Evidence Constellation</div>
        <BlurText text="每一颗星，都是一条证据" :delay="90" animate-by="words" class="blur-heading" />
        <p class="sec-note">答案是中心的恒星，证据是环绕的星点，连线是推导关系。</p>
      </div>

      <div class="starmap glass reveal">
        <!-- 画布比例贴近内容的实际包围盒：径向布局按较短边铺开，
             太高会在上下留出大片空白 -->
        <ConstellationGraph :nodes="sampleNodes" :edges="sampleEdges" :interactive="false" :width="620" :height="430" />
      </div>

      <!-- 落差文字：小字 -->
      <div class="falling-slot reveal">
        <FallingText text="点开任意一颗星，回到证据诞生的地方" trigger="scroll" :gravity="0.9"
                     :mouse-constraint-stiffness="0.12" font-size="1.05rem" />
      </div>

      <div class="legend reveal">
        <span><i style="background:#d97706"></i>答案主张</span>
        <span><i style="background:#0284c7"></i>SQL</span>
        <span><i style="background:#4f46e5"></i>检索片段</span>
        <span><i style="background:#7c3aed"></i>文档</span>
        <span><i style="background:#16a34a"></i>核验</span>
        <span><i style="background:#e11d48"></i>冲突</span>
      </div>
    </section>

    <!-- 流程 -->
    <section id="how" class="section soft">
      <div class="sec-head">
        <div class="eyebrow">How it works</div>
        <BlurText text="一条问题，四步落地" :delay="90" animate-by="words" class="blur-heading" />
      </div>
      <div class="steps">
        <div v-for="(s, i) in steps" :key="s.n" class="step reveal" :style="{ transitionDelay: i * 90 + 'ms' }">
          <div class="step-n">{{ s.n }}</div>
          <h3>{{ s.t }}</h3>
          <p>{{ s.d }}</p>
        </div>
      </div>
    </section>

    <!-- 最终 CTA：像素交换做宣传语 -->
    <section id="try" class="final">
      <div class="final-inner reveal">
        <div class="swap-slot">
          <PixelSwap trigger="hover" pattern="center" :pixel-size="52" :duration="1100"
                     :pixel-duration="420" aspect-ratio="16 / 5" easing="cubic-bezier(0.22, 1, 0.36, 1)">
            <template #first>
              <div class="swap-face"><span>现在就问一句</span></div>
            </template>
            <template #second>
              <div class="swap-face alt"><span>看证据如何点亮</span></div>
            </template>
          </PixelSwap>
        </div>
        <p class="final-note">鼠标悬停上方文字可切换</p>
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
  /* 纯色背景，不使用任何星空/渐变叠层 */
  background: var(--color-void);
  color: var(--color-text);
  overflow-x: hidden;
}

/* 仅给读屏与搜索引擎，视觉上隐藏（曲速文本是 canvas，需要有语义标题兜底） */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* ── 导航 ── */
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
  background: rgba(255, 255, 255, 0.82);
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
  color: #ffffff;
  background: linear-gradient(135deg, #2563eb, #6366f1);
  font-size: 13px;
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
  color: var(--color-body);
  text-decoration: none;
  font-size: 14px;
  transition: color 0.2s;
}
.links a:hover {
  color: var(--color-text);
}
.cta {
  padding: 8px 18px;
  border-radius: 999px;
  border: none;
  background: var(--color-text);
  color: #ffffff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s;
}
.cta:hover {
  opacity: 0.85;
}

/* ── Hero ── */
.hero {
  position: relative;
  padding: 150px 24px 40px;
  display: flex;
  justify-content: center;
}
.hero-inner {
  width: 100%;
  max-width: 1040px;
  text-align: center;
}
.pill {
  display: inline-block;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-body);
  font-size: 12.5px;
  letter-spacing: 0.02em;
}
/* 曲速文本是 WebGL 画布：必须给它一个有高度的容器 */
.warp-slot {
  margin: 18px auto 6px;
  min-height: 260px;
  width: 100%;
}
.sub {
  margin: 0 auto;
  max-width: 720px;
  color: var(--color-body);
  font-size: 16px;
  line-height: 1.9;
}
.rotator {
  display: inline-flex;
  vertical-align: bottom;
  margin: 0 4px;
  padding: 2px 10px;
  border-radius: 8px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
  font-weight: 600;
}
.hero-cta {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 30px;
}
.primary {
  padding: 13px 26px;
  border: none;
  border-radius: 10px;
  background: var(--color-accent);
  color: #ffffff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, transform 0.2s;
}
.primary:hover {
  background: #1d4ed8;
  transform: translateY(-1px);
}
.primary.big {
  padding: 15px 34px;
  font-size: 16px;
}
.ghost {
  padding: 13px 24px;
  border-radius: 10px;
  border: 1px solid var(--color-line);
  color: var(--color-text);
  text-decoration: none;
  font-size: 15px;
  background: #ffffff;
  transition: border-color 0.2s, background 0.2s;
}
.ghost:hover {
  border-color: rgba(15, 23, 42, 0.24);
  background: var(--color-ink);
}

/* ── 关键词条 ── */
.keyword-strip {
  padding: 26px 24px 10px;
  display: flex;
  justify-content: center;
}
/* TrueFocus 把每个词写死成 Tailwind 的 text-7xl（72px），直接用在页面上会撑破一行。
   这里覆盖成受控字号；选择器特异性高于工具类，无需 !important。 */
.keyword-strip :deep(span.font-black) {
  font-size: clamp(0.95rem, 1.9vw, 1.45rem);
  line-height: 1.5;
  letter-spacing: -0.01em;
  color: var(--color-text);
}

/* ── 通用区块 ── */
.section {
  padding: 92px 24px;
  max-width: 1120px;
  margin: 0 auto;
}
.section.soft {
  max-width: none;
  background: var(--color-ink);
  padding: 92px 24px;
}
.section.soft > * {
  max-width: 1120px;
  margin-left: auto;
  margin-right: auto;
}
.sec-head {
  text-align: center;
  margin-bottom: 44px;
}
.eyebrow {
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-muted);
  margin-bottom: 12px;
}
.sec-note {
  margin: 14px auto 0;
  max-width: 620px;
  color: var(--color-body);
  font-size: 15px;
  line-height: 1.8;
}
/* BlurText 根元素是 <p>，这里把它当标题用 */
.blur-heading {
  justify-content: center;
  margin: 0;
  font-size: clamp(1.8rem, 4vw, 3rem);
  font-weight: 800;
  color: var(--color-text);
  letter-spacing: -0.02em;
}

/* ── 原理 ── */
.flip-wrap {
  display: grid;
  grid-template-columns: minmax(280px, 380px) 1fr;
  gap: 48px;
  align-items: center;
}
.flipcard {
  height: 260px;
}
.face-content {
  padding: 26px;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
}
.face-content .big {
  font-size: 26px;
  font-weight: 800;
}
.face-content .big.light {
  color: var(--color-accent);
}
.face-content p {
  margin: 0;
  color: var(--color-body);
  font-size: 14px;
  line-height: 1.8;
}
.face-content .hint {
  font-size: 12px;
  color: var(--color-muted);
}
.checks {
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--color-body);
  font-size: 14px;
  line-height: 2;
}
.principle-text h3 {
  margin: 0 0 12px;
  font-size: 22px;
  font-weight: 700;
}
.principle-text p {
  margin: 0 0 14px;
  color: var(--color-body);
  font-size: 15px;
  line-height: 1.9;
}
.link {
  color: var(--color-accent);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
}

/* ── 能力卡 ── */
.feat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 20px;
}
.feat {
  border-radius: 14px;
  padding: 26px 24px;
}
.feat-icon {
  font-size: 22px;
  color: var(--color-accent);
  margin-bottom: 12px;
}
.feat h3 {
  margin: 0 0 8px;
  font-size: 17px;
  font-weight: 700;
}
.feat p {
  margin: 0;
  color: var(--color-body);
  font-size: 14px;
  line-height: 1.85;
}

/* ── 星图 ── */
.starmap {
  border-radius: 16px;
  padding: 18px;
  /* 图只有 7 个节点，铺满 1120px 会显得空；收窄到合适宽度让它读起来是「一幅图」 */
  max-width: 680px;
  margin: 0 auto;
  display: flex;
  justify-content: center;
  overflow: hidden;
}
/* 落差文字需要父容器给定高度 */
.falling-slot {
  height: 190px;
  margin: 20px auto 0;
  max-width: 760px;
  border-radius: 14px;
  border: 1px dashed var(--color-line);
  background: var(--color-ink);
  overflow: hidden;
  color: var(--color-body);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  justify-content: center;
  margin-top: 20px;
  color: var(--color-body);
  font-size: 13px;
}
.legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.legend i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}

/* ── 流程 ── */
.steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 22px;
}
.step {
  padding: 22px;
  border-radius: 14px;
  background: #ffffff;
  border: 1px solid var(--color-line);
}
.step-n {
  font-size: 13px;
  font-weight: 700;
  color: var(--color-accent);
  letter-spacing: 0.08em;
}
.step h3 {
  margin: 10px 0 8px;
  font-size: 16px;
  font-weight: 700;
}
.step p {
  margin: 0;
  color: var(--color-body);
  font-size: 13.5px;
  line-height: 1.8;
}

/* ── 底部 CTA ── */
.final {
  padding: 96px 24px 80px;
  text-align: center;
  border-top: 1px solid var(--color-line);
}
.final-inner {
  max-width: 760px;
  margin: 0 auto;
}
.swap-slot {
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid var(--color-line);
  cursor: pointer;
}
/* PixelSwap 的两个插槽层本身就是绝对定位铺满，这里只负责排版 */
.swap-face {
  display: grid;
  place-items: center;
  height: 100%;
  background: var(--color-ink);
}
.swap-face span {
  font-size: clamp(1.5rem, 4.4vw, 3rem);
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--color-text);
}
.swap-face.alt {
  background: var(--color-accent);
}
.swap-face.alt span {
  color: #ffffff;
}
.final-note {
  margin: 12px 0 26px;
  color: var(--color-muted);
  font-size: 12.5px;
}

/* ── 页脚 ── */
.foot {
  border-top: 1px solid var(--color-line);
  padding: 26px 40px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  justify-content: space-between;
  color: var(--color-muted);
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .flip-wrap {
    grid-template-columns: 1fr;
    gap: 28px;
  }
  .nav {
    padding: 14px 20px;
  }
  .links {
    display: none;
  }
  .warp-slot {
    min-height: 190px;
  }
}
</style>
