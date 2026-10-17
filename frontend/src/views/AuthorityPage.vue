<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import SideNav from '../components/SideNav.vue'
import AuthorityCandidatePanel from '../components/authority/AuthorityCandidatePanel.vue'
import AuthorityRegisterForm from '../components/authority/AuthorityRegisterForm.vue'
import AuthorityRulesPanel from '../components/authority/AuthorityRulesPanel.vue'
import { basisPageHref, failureText, useAuthority } from '../components/authority/useAuthority'
import type { ApprovalDecision, AuthorityCandidate, AuthorityRule } from '../api/types'

/**
 * 来源权威规则（/v1/authority/*，后端 datamind/authority.py）。
 *
 * 这一页只做一件有边界的事：为**一个已存在的数值冲突**登记「以哪一侧为准」的规则，
 * 再由**另一个**审批账户启用。它证明不了的东西比它证明的多，所以整页的措辞必须守住：
 *
 *   · 来源名字不构成权威：只有显式登记 + 独立启用才算数（所以来源没有默认值）。
 *   · 两侧原值一律并陈：裁决是「记录以哪一侧为准」，不是改写数据。
 *   · 登记 != 生效 != 当前适用：规则要另一账户启用，运行时还要再核验版本/审批/依据/重叠。
 *   · 失败即关闭：注册表里有一条 payload_valid=false，或同时命中两条适用规则，运行时
 *     就整体拒答（AUTHORITY_REGISTRY_INVALID / AUTHORITY_RULE_OVERLAP），没有优先级。
 *
 * 页面状态全部来自 useAuthority（随组件卸载消失，不跨账户残留操作状态）。
 */

const router = useRouter()

const {
  identity,
  roles,
  isAdmin,
  isApprover,
  identityLoading,
  identityFailure,
  rules,
  rulesLoading,
  rulesFailure,
  hasInvalidRule,
  question,
  candidates,
  candidatesLoading,
  candidatesFailure,
  candidatesQueried,
  selected,
  draft,
  registering,
  registerFailure,
  registerNotice,
  actingRuleId,
  actionFailure,
  actionNotice,
  reasons,
  loading,
  loadCandidates,
  selectCandidate,
  submitRegister,
  decide,
  bootstrap,
  refreshAll,
} = useAuthority()

/** 登记只有 admin 能做；候选也必须是「报告侧已复核 + 已独立审批」的那一种。 */
const canRegister = computed(
  () => isAdmin.value && !!selected.value && selected.value.eligible !== false && !registering.value,
)

/** 依据页链接：相对路径（绝对 http://127.0.0.1:8000 会被同源校验拒绝）。
    只认「document_id 非空 + page_no 是 ≥1 的整数」，不完整就干脆不给链接，不拼一个坏地址。 */
const basisLink = computed(() =>
  basisPageHref(draft.value.document_id, draft.value.page_no),
)

function onSearch(): void {
  void loadCandidates()
}

function onSelect(candidate: AuthorityCandidate): void {
  selectCandidate(candidate)
}

async function onAct(payload: {
  rule: AuthorityRule
  decision: ApprovalDecision
  reason: string
}): Promise<void> {
  await decide(payload.rule, payload.decision, payload.reason)
}

function backToChat(): void {
  void router.push('/app')
}

onMounted(() => {
  void bootstrap()
})
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">来源权威规则</span>
          <span class="tb-note">
            显式登记一条数值冲突的裁决规则，并由另一个审批账户启用；不登记则冲突保持未消解
          </span>
        </div>
        <button
          class="tb-action"
          type="button"
          :disabled="loading"
          data-testid="authority-refresh"
          @click="refreshAll"
        >
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body">
        <!-- 全页诚实声明：这三条不是免责话术，是这一页能证明的全部范围 -->
        <ul class="auth-honesty" data-testid="authority-honesty">
          <li>登记状态不保证当前适用。</li>
          <li>运行时还会再次核验版本、审批、依据与重叠规则。</li>
          <li>裁决保留双方原值，不能据此宣称来源一致或整体合格。</li>
          <li>仅支持工业温度数值冲突。</li>
        </ul>

        <ul class="auth-failclosed" data-testid="authority-failclosed">
          <li>
            <b>失败即关闭</b>：注册表里只要有一条规则登记校验不通过（
            <code>payload_valid=false</code>），运行时裁决就会整体失败，不会只跳过那一条。
          </li>
          <li>
            同一测量时刻命中两条都适用的规则，也会整体失败（<code>AUTHORITY_RULE_OVERLAP</code>），
            而且<b>没有优先级</b>。
          </li>
          <li>所以「已批准」不等于运行时一定生效，更不是合格结论。</li>
        </ul>

        <!-- 身份：角色不够时就别让按钮看起来能用 -->
        <p v-if="identityLoading" class="auth-hint">正在读取身份…</p>
        <div v-else-if="!identity" class="auth-anon" data-testid="authority-anon">
          <p>当前未登录，或身份不可用。此时注册表接口会以 401 失败，那<b>不是</b>「没有规则」。</p>
          <ul class="auth-note">
            <li>查询候选与登记规则：需要 admin（管理员）角色。</li>
            <li>批准 / 驳回 / 撤销：需要 approver（审批员）角色。</li>
            <li class="is-tech">admin 可以通过所有角色门；被拒时服务端返回 <code>LOGIN_REQUIRED</code>。</li>
          </ul>
        </div>
        <div v-else class="auth-identity" data-testid="authority-identity">
          <p>
            当前身份：{{ identity.display_name || identity.user_id }}（角色：{{ roles.join('、') || '无' }}）
          </p>
          <ul class="auth-note">
            <li>登记规则：需要 admin（管理员）。</li>
            <li>启用 / 驳回 / 撤销：需要 approver（审批员）。</li>
          </ul>
        </div>
        <p v-if="identityFailure" class="auth-error" data-testid="authority-identity-error">
          {{ failureText(identityFailure) }}
        </p>

        <!-- 1 · 候选 -->
        <section class="auth-section">
          <h2 class="auth-h2">1 · 查询未消解的数值冲突候选</h2>
          <p class="auth-sub">
            候选只列「两侧原值还不一致、尚无裁决」的冲突，两侧原值都会给出来。
          </p>
          <div class="auth-query">
            <input
              v-model="question"
              class="auth-input"
              type="text"
              maxlength="2000"
              placeholder="例如：2026年1月A001工序温度核验（需含 A### 零件号、工序 N、年月与「温度」）"
              data-testid="authority-question"
              @keyup.enter="onSearch"
            />
            <button
              class="auth-btn"
              type="button"
              :disabled="!isAdmin || candidatesLoading"
              data-testid="authority-search"
              @click="onSearch"
            >
              {{ candidatesLoading ? '查询中…' : '查询候选' }}
            </button>
          </div>
          <p v-if="!isAdmin" class="auth-warn" data-testid="authority-admin-required">
            查询候选与登记规则都需要 admin（管理员）角色。当前身份点这个按钮，会被服务端以 403
            PERMISSION_DENIED 拦住。
          </p>

          <p v-if="candidatesFailure" class="auth-error" data-testid="authority-candidates-error">
            {{ failureText(candidatesFailure) }}
          </p>
          <p v-if="candidatesLoading" class="auth-hint">正在重新解析问题并读取冲突…</p>
          <ul
            v-else-if="candidatesQueried && !candidates.length && !candidatesFailure"
            class="auth-note"
            data-testid="authority-candidates-empty"
          >
            <li>这个问题下没有未消解的数值冲突候选。</li>
            <li>查不到候选不等于两侧一致：只有「未消解」的冲突才会出现在这里。</li>
            <li>缺报告、单位不可比、没有匹配测量，都不构成本页能裁决的冲突。</li>
          </ul>

          <AuthorityCandidatePanel
            v-for="c in candidates"
            :key="c.candidate_sha256 + c.context.scope.measurement_id"
            :candidate="c"
            :selected="selected?.candidate_sha256 === c.candidate_sha256"
            :busy="registering"
            @select="onSelect"
          />
        </section>

        <!-- 2 · 登记 -->
        <section v-if="selected && isAdmin" class="auth-section">
          <h2 class="auth-h2">2 · 登记规则（提交后仍是待审批）</h2>
          <p class="auth-sub">
            登记的是上面选中的那条候选。两侧原值仍以上方面板为准，登记不会改写任何一侧。
          </p>
          <p v-if="registerNotice" class="auth-ok" data-testid="authority-register-notice">
            {{ registerNotice }}
          </p>
          <AuthorityRegisterForm
            v-model="draft"
            :produced-at="selected.produced_at"
            :can-register="canRegister"
            :registering="registering"
            :failure="registerFailure"
            :basis-link="basisLink"
            @submit="submitRegister"
          />
        </section>
        <!-- 登记成功后选区会被清空、表单随之消失；上下文变化时也会清空表单。这两条消息
             不能跟着表单一起消失，否则就等于「悄悄失败/悄悄成功」——所以在这里补渲染。 -->
        <template v-else>
          <p v-if="registerNotice" class="auth-ok" data-testid="authority-register-notice">
            {{ registerNotice }}
          </p>
          <p v-if="registerFailure" class="auth-error" data-testid="authority-register-error">
            {{ failureText(registerFailure) }}
          </p>
        </template>

        <!-- 3 · 规则注册表 -->
        <section class="auth-section">
          <h2 class="auth-h2">3 · 已登记规则</h2>
          <p class="auth-sub">
            服务端没有单独的「状态」字段：一条规则的状态就是它链尾那条事件的结论，还没有事件就是待审批。
          </p>

          <ul v-if="hasInvalidRule" class="auth-note" data-testid="authority-registry-invalid">
            <li>注册表里存在「登记完整性校验失败」的规则。</li>
            <li>
              这种情况下运行时裁决会整体失败，不会退化成「按其余有效规则继续裁决」。
            </li>
            <li class="is-tech">失败时服务端返回 <code>AUTHORITY_REGISTRY_INVALID</code>。</li>
          </ul>
          <p v-if="actionNotice" class="auth-ok" data-testid="authority-action-notice">
            {{ actionNotice }}
          </p>
          <p v-if="actionFailure" class="auth-error" data-testid="authority-action-error">
            {{ failureText(actionFailure) }}
          </p>
          <p v-if="rulesFailure" class="auth-error" data-testid="authority-rules-error">
            {{ failureText(rulesFailure) }}
          </p>

          <p v-if="rulesLoading" class="auth-hint">正在加载注册表…</p>
          <p
            v-else-if="!rules.length && !rulesFailure"
            class="auth-empty"
            data-testid="authority-rules-empty"
          >
            还没有登记规则，来源冲突保持未消解。
          </p>
          <AuthorityRulesPanel
            v-else-if="rules.length"
            v-model:reasons="reasons"
            :rules="rules"
            :is-approver="isApprover"
            :current-user-id="identity?.user_id ?? ''"
            :acting-rule-id="actingRuleId"
            @act="onAct"
          />
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 auth- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会
   出现「部分属性被工具类接管」的怪象（仓库里已出过两次事故）。 */
.mainpage {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}
.center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--color-line);
}
.tb-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
/* 标题与「模型接入」页一致：蓝色、15px、600（原来是 16px 灰黑，属离群值） */
.tb-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.tb-note {
  font-size: 14px;
  color: var(--color-text);
}
/* 顶栏按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.tb-action {
  margin-left: auto;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.tb-action:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.tb-action:disabled {
  opacity: 0.6;
  cursor: default;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 28px 48px;
  max-width: 1120px;
  width: 100%;
}
/* 去框：原来两处说明都是「圆角 + 浅底 + 描边」的卡片，用户反馈"AI 味太重"。
   改成左侧一条细竖线分组（蓝=说明，橙=警示），不加底色、不加圆角。
   这是编辑排版里的常见做法：用一条线建立层次，比整块填色克制得多。 */
.auth-honesty {
  margin: 0 0 16px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid var(--color-accent);
  border-radius: 0;
  background: none;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--color-text);
}
.auth-failclosed {
  margin: 0 0 18px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--color-text);
}
.auth-failclosed b {
  font-weight: 600;
  color: #b45309;
}
/* 说明文字按条列出：每行一条、前面一个蓝点（与 GovernancePage 的 .gov-note、
   FieldsPage 的 .fields-note 同一套写法）。类名前缀 auth- 不会撞 Tailwind 工具类。 */
.auth-note {
  margin: 6px 0 0;
  padding: 0;
  max-width: 1000px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text);
}
.auth-note > li {
  position: relative;
  padding-left: 15px;
}
.auth-note > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}
.auth-note b {
  font-weight: 600;
  color: var(--color-accent);
}
.auth-note .is-tech {
  font-size: 12.5px;
  color: var(--color-body);
}
.auth-note code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  background: var(--color-ink);
  border-radius: 4px;
  padding: 1px 5px;
}
.auth-identity > p,
.auth-anon > p {
  margin: 0 0 4px;
}
.auth-honesty code,
.auth-failclosed code,
.auth-sub code,
.auth-empty code,
.auth-note code {
  font-size: 12.5px;
}
.auth-section {
  margin-bottom: 26px;
}
.auth-h2 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.auth-sub {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.auth-query {
  display: flex;
  gap: 10px;
  margin-bottom: 8px;
}
.auth-input {
  flex: 1;
  min-width: 0;
  font: inherit;
  font-size: 14.5px;
  padding: 8px 11px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-text);
}
/* 查询按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.auth-btn {
  flex-shrink: 0;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 8px 18px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.auth-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.auth-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.auth-identity,
.auth-anon {
  margin: 0 0 18px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--color-body);
}
.auth-anon {
  padding: 10px 14px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.auth-hint,
.auth-empty {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text);
}
.auth-empty {
  padding: 22px 0;
}
.auth-warn {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.8;
  color: #d97706;
}
.auth-ok {
  margin: 0 0 10px;
  font-size: 13.5px;
  line-height: 1.8;
  color: #16a34a;
  word-break: break-all;
}
.auth-error {
  margin: 0 0 10px;
  font-size: 14px;
  line-height: 1.8;
  color: #e11d48;
  word-break: break-word;
}
</style>
