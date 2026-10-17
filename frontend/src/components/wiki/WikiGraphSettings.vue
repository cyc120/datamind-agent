<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  APPEARANCE_BOUNDS,
  DEFAULT_APPEARANCE,
  DEFAULT_COLOR_STATE,
  DEFAULT_FILTERS,
  DEFAULT_FORCE_PARAMS,
  ENTRY_TYPE_OPTIONS,
  FORCE_PARAM_BOUNDS,
  LAYER_OPTIONS,
  PUBLICATION_STATUS_OPTIONS,
  clampDotScale,
  clampForceParams,
  clampLinkOpacity,
  isDefaultForceParams,
} from './forceLayout'
import type {
  ForceParams,
  WikiAppearanceState,
  WikiColorState,
  WikiFilterState,
} from './forceLayout'

/* ============================================================================
 * 右上角紧凑的**可折叠设置面板**（契约 §8.3 的四个组：筛选 / 颜色组 / 外观 / 力度）。
 *
 * 三条实现约定：
 *
 *  1. **用原生 `<details>` / `<summary>` 做折叠**，不自己写 div + 点击：
 *     键盘（Tab 聚焦 + Enter/Space 展开）、读屏器（展开状态是原生语义）都是白送的。
 *     代价是字号/箭头要自己压，所以下面只覆盖需要覆盖的属性（见 Tailwind 重名坑）。
 *
 *  2. **筛选只作用于当前授权集合**（契约 §8.3）：这里是受控组件，只把用户的选择
 *     回传上去，自己不请求、不过滤、不缓存任何数据 —— 过滤**永远**发生在服务端，
 *     因为客户端过滤会把"看不到"变成"不存在"，那正是契约 §5 要防的事。
 *
 *  3. **力度只改变布局**（契约 §8.3 末段）：四个滑块的值全部经 `clampForceParams`
 *     夹在冻结边界内，并带"恢复默认值"；它不可能影响任何关系或审核结论 ——
 *     本组件根本不持有关系数据。
 * ========================================================================== */

const filters = defineModel<WikiFilterState>('filters', { required: true })
const colors = defineModel<WikiColorState>('colors', { required: true })
const appearance = defineModel<WikiAppearanceState>('appearance', { required: true })
const forces = defineModel<ForceParams>('forces', { required: true })

const props = withDefaults(
  defineProps<{
    /** 服务端返回的图层（用于显示"服务端实际返回的图层"，与选择不一致时给出提示） */
    activeLayer?: string
    /** 已达到 100 节点 / 300 关系上限时为 true：此时提示"缩小范围"而不是"继续加载" */
    limitReached?: boolean
    /** 正在加载：禁用"重新排布"以免并发重排 */
    busy?: boolean
    /** 图例内容：颜色组启用后由父组件按当前节点集合算好传进来 */
    legend?: Array<{ key: string; label: string; color: string; count: number }>
    /** 图例的说明文字（按类型 / 按分类，两句话不一样） */
    legendNote?: string
    /** 低运动偏好是否由**系统**设置触发（true 时不允许用户关掉，只能说明） */
    systemReducedMotion?: boolean
  }>(),
  {
    activeLayer: 'entries',
    limitReached: false,
    busy: false,
    legend: () => [],
    legendNote: '',
    systemReducedMotion: false,
  },
)

const emit = defineEmits<{
  /** 用户明确要求重新排布（契约允许重排的三种情形之一） */
  (e: 'relayout'): void
  /** 暂停 / 恢复布局 */
  (e: 'toggle-pause'): void
  /** 恢复默认灰色 */
  (e: 'reset-colors'): void
}>()

/** 四组的展开状态。默认全收起：参考图的设置面板是"紧凑"的，展开一个才占地方。 */
const openGroups = ref<Record<string, boolean>>({
  filters: true,
  colors: false,
  appearance: false,
  forces: false,
})

function toggleGroup(key: string, event: Event): void {
  // <details> 的 open 由浏览器自己切；这里只同步我们记的状态（受控显示"已启用项数"）。
  const target = event.target as HTMLDetailsElement | null
  openGroups.value = { ...openGroups.value, [key]: !!target?.open }
}

const layerMismatch = computed(
  () => !!props.activeLayer && props.activeLayer !== filters.value.layer,
)

/** 各组标题上的小徽标：收起时也要看得出"这一组里有非默认设置"。 */
const filterSummary = computed(() => {
  const parts: string[] = []
  if (filters.value.layer !== DEFAULT_FILTERS.layer) {
    parts.push(LAYER_OPTIONS.find((o) => o.value === filters.value.layer)?.label ?? filters.value.layer)
  }
  if (filters.value.type) {
    parts.push(ENTRY_TYPE_OPTIONS.find((o) => o.value === filters.value.type)?.label ?? filters.value.type)
  }
  if (filters.value.status) {
    parts.push(
      PUBLICATION_STATUS_OPTIONS.find((o) => o.value === filters.value.status)?.label ??
        filters.value.status,
    )
  }
  if (filters.value.category.trim()) parts.push(`分类「${filters.value.category.trim()}」`)
  if (filters.value.q.trim()) parts.push(`关键词「${filters.value.q.trim()}」`)
  return parts.join(' · ')
})

const appearanceSummary = computed(() => {
  if (appearance.value.paused) return '已暂停'
  if (appearance.value.lowMotion) return '低运动'
  const off: string[] = []
  if (!appearance.value.showLabels) off.push('标题已隐藏')
  if (Math.abs(appearance.value.dotScale - DEFAULT_APPEARANCE.dotScale) > 1e-9) off.push('圆点已缩放')
  if (Math.abs(appearance.value.linkOpacity - DEFAULT_APPEARANCE.linkOpacity) > 1e-9) {
    off.push('细线透明度已改')
  }
  return off.join(' · ')
})

const forcesAreDefault = computed(() => isDefaultForceParams(forces.value))

/** 滑块双向绑定：值先夹边界再写回，所以 UI 上不可能出现超界值。 */
function setForce(key: keyof ForceParams, event: Event): void {
  const raw = Number((event.target as HTMLInputElement).value)
  forces.value = clampForceParams({ ...forces.value, [key]: raw })
}

function resetForces(): void {
  forces.value = { ...DEFAULT_FORCE_PARAMS }
  emit('relayout')
}

function setDotScale(event: Event): void {
  appearance.value = {
    ...appearance.value,
    dotScale: clampDotScale(Number((event.target as HTMLInputElement).value)),
  }
}

function setLinkOpacity(event: Event): void {
  appearance.value = {
    ...appearance.value,
    linkOpacity: clampLinkOpacity(Number((event.target as HTMLInputElement).value)),
  }
}

/** 系统低运动偏好生效时复选框是禁用且勾选的，所以这里只处理"用户自己勾"的那条路径。 */
function toggleLowMotion(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  appearance.value = { ...appearance.value, lowMotion: checked }
}

function toggleLabels(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  appearance.value = { ...appearance.value, showLabels: checked }
}

function toggleColors(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  colors.value = { ...colors.value, enabled: checked }
}

function setColorBy(by: 'type' | 'category'): void {
  colors.value = { ...colors.value, by }
}

function resetAppearance(): void {
  // 低运动与暂停是"当前状态"，不是外观偏好，所以恢复默认值时不碰它们。
  appearance.value = {
    ...appearance.value,
    showLabels: DEFAULT_APPEARANCE.showLabels,
    dotScale: DEFAULT_APPEARANCE.dotScale,
    linkOpacity: DEFAULT_APPEARANCE.linkOpacity,
  }
}

function resetFilters(): void {
  filters.value = { ...DEFAULT_FILTERS, layer: filters.value.layer }
}

function resetColors(): void {
  colors.value = { ...DEFAULT_COLOR_STATE }
  emit('reset-colors')
}

/** 图层切换会**重新取得授权数据**（契约 §9.2），所以由父组件监听 filters.layer。 */
const FORCE_ROWS: Array<{
  key: keyof ForceParams
  label: string
  hint: string
  min: number
  max: number
  step: number
  format: (value: number) => string
}> = [
  {
    key: 'repulsion',
    label: '排斥力',
    hint: '节点互相推开的强度；调大 = 更松散',
    min: FORCE_PARAM_BOUNDS.repulsion.min,
    max: FORCE_PARAM_BOUNDS.repulsion.max,
    step: FORCE_PARAM_BOUNDS.repulsion.step,
    format: (v) => v.toFixed(2),
  },
  {
    key: 'linkStrength',
    label: '连接拉力',
    hint: '有关系的节点互相靠拢的强度；调大 = 更紧凑',
    min: FORCE_PARAM_BOUNDS.linkStrength.min,
    max: FORCE_PARAM_BOUNDS.linkStrength.max,
    step: FORCE_PARAM_BOUNDS.linkStrength.step,
    format: (v) => v.toFixed(2),
  },
  {
    key: 'linkDistance',
    label: '连接长度',
    hint: '相邻节点的理想距离（布局单位，不是像素）',
    min: FORCE_PARAM_BOUNDS.linkDistance.min,
    max: FORCE_PARAM_BOUNDS.linkDistance.max,
    step: FORCE_PARAM_BOUNDS.linkDistance.step,
    format: (v) => String(Math.round(v)),
  },
  {
    key: 'centerGravity',
    label: '向中心聚合',
    hint: '向画面中心收拢的程度；0 = 不收拢',
    min: FORCE_PARAM_BOUNDS.centerGravity.min,
    max: FORCE_PARAM_BOUNDS.centerGravity.max,
    step: FORCE_PARAM_BOUNDS.centerGravity.step,
    format: (v) => v.toFixed(2),
  },
]
</script>

<template>
  <div class="wg-panel" role="group" aria-label="图谱设置">
    <p class="wg-panel-head">
      图谱设置
      <span class="wg-panel-head-note">四组，均可折叠</span>
    </p>

    <!-- 中间是可滚动的四组；页脚固定，主操作永远在看得见的地方。
         WHY：1440×900 下画布只有 576px 高、面板 552px，而四组标题 + 「筛选」正文本身
         就有 638px —— 旧结构把「清除筛选 / 重新排布」放在正文末尾，等于把它们藏到折叠线以下。 -->
    <div class="wg-groups">

    <!-- ── 组 1：筛选 ───────────────────────────────────────────────────── -->
    <details class="wg-group" :open="openGroups.filters" @toggle="toggleGroup('filters', $event)">
      <summary class="wg-summary">
        <span class="wg-summary-title">筛选</span>
        <span v-if="filterSummary" class="wg-summary-note">{{ filterSummary }}</span>
        <span v-else class="wg-summary-note wg-muted">未筛选</span>
      </summary>
      <div class="wg-body">
        <ul class="wg-note-list">
          <li>筛选<strong>只在当前授权范围内</strong>生效：它会让服务端重新取数，不是把已有权限的对象藏起来。</li>
          <li>所以「筛掉」不等于「不存在」：每次取数服务端都会回报本次实际考虑与可见的对象数。</li>
        </ul>

        <label class="wg-field wg-field-inline">
          <span class="wg-label">图层</span>
          <select v-model="filters.layer" class="wg-control">
            <option v-for="option in LAYER_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <p v-if="layerMismatch" class="wg-warn">
          当前显示的是服务端返回的「{{ activeLayer }}」图层，与你选的不一致，正在重新取数。
        </p>
        <p v-if="filters.layer !== 'entries'" class="wg-note">
          关键词、词条类型、分类、知识状态<strong>只对「词条关系」图层有意义</strong>。
          换到另外两层时这些条件不会发给服务端（服务端拒绝静默忽略这类筛选，传了会报错），
          所以它们在这一层是禁用的。
        </p>

        <label class="wg-field wg-field-inline">
          <span class="wg-label">词条类型</span>
          <select v-model="filters.type" class="wg-control" :disabled="filters.layer !== 'entries'">
            <option value="">不限</option>
            <option v-for="option in ENTRY_TYPE_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="wg-field wg-field-inline">
          <span class="wg-label">分类</span>
          <input
            v-model="filters.category"
            class="wg-control"
            type="text"
            maxlength="64"
            placeholder="例如：工业设备"
            :disabled="filters.layer !== 'entries'"
          />
        </label>

        <label class="wg-field wg-field-inline">
          <span class="wg-label">知识状态</span>
          <select v-model="filters.status" class="wg-control" :disabled="filters.layer !== 'entries'">
            <option value="">不限</option>
            <option v-for="option in PUBLICATION_STATUS_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="wg-field wg-field-inline">
          <span class="wg-label">关键词</span>
          <input
            v-model="filters.q"
            class="wg-control"
            type="search"
            maxlength="200"
            placeholder="标题 / 别名 / 分类"
            :disabled="filters.layer !== 'entries'"
          />
        </label>

        <!-- 两组字段的完整口径收进一个折叠块（默认收起）。
             实测：这两条说明各占 132px（268px 宽时本组正文 647px 的 41%），默认摊开会把
             下面三组的标题挤出可视区。收起后仍是一行蓝字入口，展开还是原来的
             「无框说明块 + 蓝点列表」，措辞一个字没删。 -->
        <details class="wg-more">
          <summary class="wg-more-summary">口径说明：知识状态与关键词各筛什么</summary>
          <ul class="wg-note-list">
            <li>
              <strong>知识状态</strong>：这里筛的是<strong>发布流程</strong>状态（草稿 / 待复核 / 已发布 / 已归档）。
              核验状态与来源有效性是另外两套独立状态，不会因为这一项被筛掉或被合并。
            </li>
            <li>
              <strong>关键词</strong>：<strong>只匹配标题、别名与分类</strong>，不搜正文 —— 中文正文的检索方案
              还没定，服务端会在返回结果里如实声明这次搜索覆盖了哪些字段。
            </li>
          </ul>
        </details>
      </div>
    </details>

    <!-- ── 组 2：颜色组 ─────────────────────────────────────────────────── -->
    <details class="wg-group" :open="openGroups.colors" @toggle="toggleGroup('colors', $event)">
      <summary class="wg-summary">
        <span class="wg-summary-title">颜色组</span>
        <span class="wg-summary-note">
          {{ colors.enabled ? (colors.by === 'type' ? '按词条类型着色' : '按分类着色') : '初始灰色' }}
        </span>
      </summary>
      <div class="wg-body">
        <label class="wg-check">
          <input type="checkbox" :checked="colors.enabled" @change="toggleColors" />
          <span>启用颜色组</span>
        </label>
        <p class="wg-note">
          关闭时节点是<strong>深浅灰色</strong>（初始状态）。颜色<strong>只</strong>区分词条类型或分类，
          不表示置信度、权威、审核结果或重要程度。
        </p>

        <fieldset class="wg-fieldset" :disabled="!colors.enabled">
          <legend class="wg-label">着色依据</legend>
          <label class="wg-check">
            <input
              type="radio"
              name="wg-color-by"
              value="type"
              :checked="colors.by === 'type'"
              @change="setColorBy('type')"
            />
            <span>按词条类型</span>
          </label>
          <label class="wg-check">
            <input
              type="radio"
              name="wg-color-by"
              value="category"
              :checked="colors.by === 'category'"
              @change="setColorBy('category')"
            />
            <span>按分类</span>
          </label>
        </fieldset>

        <div class="wg-legend">
          <p class="wg-label">图例</p>
          <ul v-if="colors.enabled && legend.length" class="wg-legend-list">
            <li v-for="item in legend" :key="item.key" class="wg-legend-item">
              <span class="wg-swatch" :style="{ background: item.color }" aria-hidden="true"></span>
              <span class="wg-legend-text">{{ item.label }}</span>
              <span class="wg-legend-count">{{ item.count }} 个（已加载）</span>
            </li>
          </ul>
          <p v-else-if="colors.enabled" class="wg-note">当前已加载集合里没有可归类的节点。</p>
          <p v-else class="wg-note">未启用颜色组，全部节点使用初始灰色。</p>
          <p v-if="colors.enabled && legendNote" class="wg-hint">{{ legendNote }}</p>
          <p class="wg-hint">
            冲突与核验状态<strong>不靠颜色表达</strong>：节点上另加文字标识（如“冲突”“核验不足”），
            并写进图外的词条目录里，不用辨色也能读到。
          </p>
        </div>

        <div class="wg-actions">
          <button type="button" class="wg-btn" @click="resetColors">恢复默认灰色</button>
        </div>
      </div>
    </details>

    <!-- ── 组 3：外观 ───────────────────────────────────────────────────── -->
    <details class="wg-group" :open="openGroups.appearance" @toggle="toggleGroup('appearance', $event)">
      <summary class="wg-summary">
        <span class="wg-summary-title">外观</span>
        <span class="wg-summary-note">{{ appearanceSummary || '默认' }}</span>
      </summary>
      <div class="wg-body">
        <label class="wg-check">
          <input type="checkbox" :checked="appearance.showLabels" @change="toggleLabels" />
          <span>显示标题</span>
        </label>
        <p class="wg-hint">
          标题默认可读。缩放后按可用空间<strong>减少非选中标签</strong>，但选中节点与其邻域的标题优先显示；
          完整标题始终能在图外的词条目录与详情里读到。
        </p>

        <label class="wg-field">
          <span class="wg-label">
            圆点尺寸缩放
            <span class="wg-value">{{ appearance.dotScale.toFixed(1) }}×</span>
          </span>
          <input
            class="wg-range"
            type="range"
            :min="APPEARANCE_BOUNDS.dotScale.min"
            :max="APPEARANCE_BOUNDS.dotScale.max"
            :step="APPEARANCE_BOUNDS.dotScale.step"
            :value="appearance.dotScale"
            @input="setDotScale"
          />
          <span class="wg-hint">
            圆点大小<strong>只</strong>表示“当前授权且已加载的连接数”，采用有上下限的缩放；
            它不表示置信度、权威或全库连接数。孤立节点（连接数 0）仍按下限半径显示，可见、可点击。
          </span>
        </label>

        <label class="wg-field">
          <span class="wg-label">
            细线透明度
            <span class="wg-value">{{ appearance.linkOpacity.toFixed(2) }}</span>
          </span>
          <input
            class="wg-range"
            type="range"
            :min="APPEARANCE_BOUNDS.linkOpacity.min"
            :max="APPEARANCE_BOUNDS.linkOpacity.max"
            :step="APPEARANCE_BOUNDS.linkOpacity.step"
            :value="appearance.linkOpacity"
            @input="setLinkOpacity"
          />
          <span class="wg-hint">
            细线代表<strong>实际已保存且允许访问</strong>的关系。选中节点后，关系的类型、方向与依据会在
            图下列出。
          </span>
        </label>

        <label class="wg-check">
          <input
            type="checkbox"
            :checked="appearance.lowMotion || systemReducedMotion"
            :disabled="systemReducedMotion"
            @change="toggleLowMotion"
          />
          <span>低运动</span>
        </label>
        <p class="wg-hint">
          <template v-if="systemReducedMotion">
            系统已开启“减少动态效果”，低运动已<strong>强制生效</strong>且不能在此关闭：排布一次算完并
            静态呈现，不会持续移动节点。
          </template>
          <template v-else>
            低运动 = 一次算完并静态呈现（仍然受 300 次 / 5 秒上限约束），不做逐帧动画。
          </template>
        </p>

        <div class="wg-actions">
          <button type="button" class="wg-btn" @click="resetAppearance">恢复默认外观</button>
          <button type="button" class="wg-btn wg-btn-primary" @click="emit('toggle-pause')">
            {{ appearance.paused ? '恢复布局' : '暂停布局' }}
          </button>
        </div>
        <p class="wg-hint">
          暂停后节点不再持续移动；恢复会从当前位置续算，不会跳回初始位置。
        </p>
      </div>
    </details>

    <!-- ── 组 4：力度 ───────────────────────────────────────────────────── -->
    <details class="wg-group" :open="openGroups.forces" @toggle="toggleGroup('forces', $event)">
      <summary class="wg-summary">
        <span class="wg-summary-title">力度</span>
        <span class="wg-summary-note">{{ forcesAreDefault ? '默认值' : '已调整' }}</span>
      </summary>
      <div class="wg-body">
        <p class="wg-note">
          力度<strong>只改变布局</strong>：它不会新增、删除或改写任何关系，也不影响任何审核结果。
          参数都有上下限，越界会被夹回边界。
        </p>
        <label v-for="row in FORCE_ROWS" :key="row.key" class="wg-field">
          <span class="wg-label">
            {{ row.label }}
            <span class="wg-value">{{ row.format(forces[row.key]) }}</span>
          </span>
          <input
            class="wg-range"
            type="range"
            :min="row.min"
            :max="row.max"
            :step="row.step"
            :value="forces[row.key]"
            @input="setForce(row.key, $event)"
          />
          <span class="wg-hint">{{ row.hint }}</span>
        </label>
        <div class="wg-actions">
          <button type="button" class="wg-btn" :disabled="forcesAreDefault" @click="resetForces">
            恢复默认值
          </button>
          <button type="button" class="wg-btn wg-btn-primary" :disabled="busy" @click="emit('relayout')">
            按新力度重排
          </button>
        </div>
        <p class="wg-hint">
          上限（冻结，不可调）：单次最多 100 节点 / 300 关系；迭代最多 300 次或运行 5 秒，
          先到即停。
        </p>
      </div>
    </details>
    </div>

    <!-- 页脚：与「筛选」直接相关的两个动作，四组都折叠起来时也仍然可用。 -->
    <div class="wg-footer">
      <button type="button" class="wg-btn" :disabled="busy" @click="resetFilters">清除筛选</button>
      <button type="button" class="wg-btn wg-btn-primary" :disabled="busy" @click="emit('relayout')">
        重新排布
      </button>
    </div>
  </div>
</template>

<style scoped>
/* ⚠ 样式作用域的两个坑（本仓库踩过三次）：
   1. **类名不能与 Tailwind 工具类重名**（`.collapse` → visibility:collapse、
      `.table-caption` → display:table-caption 都出过事故）。这里全部用 `wg-` 前缀，
      Tailwind 工具类没有 `wg-` 开头的，所以不可能撞。
   2. scoped 只对**本组件声明过的属性**优先；没声明的属性会被工具类接管。
      所以下面每个类都把需要的属性写全，不指望继承。 */

.wg-panel {
  /* 宽度由父组件给（WikiForceGraph 用它把空态文字让开浮层），单独用时回落 300px。
     300 而不是原来的 268：中文 13.5px 在 268px 里一行只剩 15 个字，
     说明类句子会被折成 3~4 行；宽 32px 后同样的句子少一行。 */
  width: var(--wg-settings-width, 300px);
  /* 100% 参照的是父容器（同时给了 top/bottom）的高度，所以面板永远不会高于画布 */
  max-height: 100%;
  /* 外层不再滚动：头、四组、页脚三段式，只有中间那段滚（.wg-groups）。
     否则 sticky 页脚会压在正文最后一行上（实测会盖住「口径说明」那一行）。 */
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--color-panel);
  border: 1px solid var(--color-line);
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 8px 24px rgba(15, 23, 42, 0.06);
  font-size: 13.5px;
  color: var(--color-text);
}

/* 四组的滚动容器：min-height:0 是 flex 子项能滚的前提（否则按内容撑高、永远不滚）。 */
.wg-groups {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

/* 页脚：蓝色主操作常驻。与「筛选」直接相关，四组都收起时也仍然可用。 */
.wg-footer {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 10px;
  border-top: 1px solid var(--color-line);
  background: var(--color-panel);
}

/* 面板标题与其它页统一：蓝色、15px、600（原来是 13.5px 灰黑） */
.wg-panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin: 0;
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-line);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--color-accent);
}

.wg-panel-head-note {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--color-text);
}

.wg-group {
  border-bottom: 1px solid var(--color-line);
}

.wg-group:last-child {
  border-bottom: 0;
}

.wg-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  padding: 8px 10px;
  cursor: pointer;
  list-style: none;
  user-select: none;
}

/* 统一去掉各浏览器给 summary 的默认三角，改用一个字符标记（保证键盘焦点样式一致）。 */
.wg-summary::-webkit-details-marker {
  display: none;
}

.wg-summary::before {
  content: '▸';
  flex: 0 0 auto;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.6;
}

.wg-group[open] > .wg-summary::before {
  content: '▾';
}

.wg-summary:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
  border-radius: 6px;
}

.wg-summary-title {
  font-weight: 600;
}

.wg-summary-note {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12.5px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wg-body {
  display: flex;
  flex-direction: column;
  /* 组内从 10 加到 12：控件与说明之间靠间距分组，比加框更轻（用户认可的是无框说明块）。 */
  gap: 12px;
  padding: 2px 10px 12px 22px;
}

.wg-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

/* 「筛选」的五个项走**行内**排布：标签在左、控件在右。
   旧布局每项 54px（标签一行 + 控件一行），五项就是 270px —— 面板里最大的一块高度，
   而标签本来就是短词，独占一行不划算。行内后每项约 30px，标签仍在可见位置
   （不是 placeholder、也不是 tooltip：键盘与读屏器照旧读到「图层」这个 name）。 */
.wg-field-inline {
  flex-direction: row;
  align-items: center;
  gap: 10px;
}

.wg-field-inline > .wg-label {
  flex: 0 0 56px;
  justify-content: flex-start;
}

/* 行内时控件自己占满剩余宽度：.wg-control 的 width:100% 在这里会把行撑破，所以覆盖掉。 */
.wg-field-inline > .wg-control {
  flex: 1 1 auto;
  width: auto;
  min-width: 0;
}

.wg-fieldset {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 6px 8px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
}

.wg-label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-body);
}

.wg-value {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--color-muted);
}

.wg-control {
  width: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  font-size: 13.5px;
  font-family: inherit;
}

.wg-control:disabled {
  background: var(--color-ink);
  color: var(--color-muted);
}

.wg-control:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wg-range {
  width: 100%;
  accent-color: var(--color-accent);
}

.wg-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  color: var(--color-body);
}

.wg-note {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--color-body);
}

/* 说明按条列出：每行一条、前面一个蓝点（与 WikiPage 的 .wk-note 同一套写法）。
   类名前缀 wg- 不会撞 Tailwind 工具类。 */
.wg-note-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--color-body);
}
.wg-note-list > li {
  position: relative;
  padding-left: 13px;
}
.wg-note-list > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-accent);
}
.wg-note-list strong {
  color: var(--color-text);
}

/* ── 口径说明：字段级长解释的折叠块（原生 <details>，与上面四组同一套约定） ──
   收起时只占一行；展开后里面的列表仍是「无框说明块 + 蓝点」，与用户认可过的观感一致。
   标题用主色蓝（不是灰黑）：它是一行可点的入口，不是正文。 */
.wg-more {
  border-top: 1px dashed var(--color-line);
  padding-top: 9px;
}

.wg-more-summary {
  display: flex;
  align-items: baseline;
  gap: 6px;
  cursor: pointer;
  list-style: none;
  user-select: none;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-accent);
}

.wg-more-summary::-webkit-details-marker {
  display: none;
}

.wg-more-summary::before {
  content: '▸';
  flex: 0 0 auto;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.6;
}

.wg-more[open] > .wg-more-summary::before {
  content: '▾';
}

.wg-more-summary:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
  border-radius: 6px;
}

/* 展开后的列表与标题之间留一口气（.wg-note-list 自己 margin:0，这里要更具体的选择器） */
.wg-more > .wg-note-list {
  margin-top: 7px;
}

.wg-hint {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--color-text);
}

.wg-muted {
  color: var(--color-muted);
}

.wg-warn {
  margin: 0;
  padding: 4px 8px;
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.1);
  color: #b45309;
  font-size: 12.5px;
  line-height: 1.5;
}

.wg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.wg-btn {
  padding: 4px 12px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}

.wg-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}

.wg-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

/* 禁用态只降透明度、不改文字色：底色已是主色蓝，压成灰字会变成「蓝底灰字」
   （实测对比度极差）。与「模型接入」页 .mp-btn:disabled 一致。 */
.wg-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* primary 变体现在与基础样式等价（保留类名，避免动模板） */
.wg-btn-primary {
  border-color: transparent;
  background: var(--color-accent);
  color: #ffffff;
  font-weight: 600;
}

.wg-legend-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}

.wg-legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}

.wg-swatch {
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 999px;
  border: 1px solid rgba(15, 23, 42, 0.15);
}

.wg-legend-text {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--color-body);
}

.wg-legend-count {
  flex: 0 0 auto;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
</style>
