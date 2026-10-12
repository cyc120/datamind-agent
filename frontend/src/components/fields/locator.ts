/**
 * 字段复核 —— 证据引用（evidence_ref）的绘制判定。
 *
 * WHY 必须是一个独立的纯函数模块：
 *   本屏的定位框是「证据来自哪一段原文」的唯一视觉证明。画错一个框比不画更糟——
 *   用户会把一个**猜出来的**区域当成证据来源，从此信任一条并没有被定位支持的事实。
 *   所以这里只做一件事：判断一条引用是否**真的可绘制**；不满足条件时给出中文原因，
 *   视图层只允许「画」或「出标签」两种结果，没有第三条路。
 *
 * 本屏引用的坐标系是 PDF 点（pdf_points）：bbox 是 [x0,y0,x1,y1]，page_size 是
 * [宽,高]，二者同一坐标系，因此 SVG 可以直接用 `viewBox="0 0 w h"` 铺在页面图上，
 * 不需要任何缩放推断（推断就是猜测）。若坐标系不是 pdf_points，本模块一律拒绝——
 * 别的坐标系（例如 top-left 像素、CAD 世界坐标）没有与之匹配的页面图映射。
 */

/** 后端 `FactEvidenceRef` 的结构子集：只声明判定需要的键，其余原样透传。 */
export interface EvidenceRefLike {
  role?: string | null
  field?: string | null
  field_value?: string | null
  block_id?: string | null
  page_no?: number | null
  /** [x0,y0,x1,y1]，单位见 coordinate_system */
  bbox?: number[] | null
  /** [宽,高]，与 bbox 同坐标系 */
  page_size?: number[] | null
  coordinate_system?: string | null
  /**
   * 该引用所在页相对**页面图**的旋转角度（parse contract 的 `rotation_degrees`）。
   * 只有后端**显式声明**且不是 360 的整数倍时才拒绝绘制；判定理由见 locatorVerdict。
   */
  rotation_degrees?: number | null
  [key: string]: unknown
}

/** 可绘制：所有坐标都已校验，视图层可直接进 SVG。 */
export interface LocatorDrawable {
  drawable: true
  /** 1 起的页码 */
  pageNo: number
  /** 已在 bbox 合法性校验之后的四元组 */
  bbox: [number, number, number, number]
  /** 页面宽高（同坐标系） */
  pageSize: [number, number]
}

/** 不可绘制：绝不绘制，只显示 `无法绘制：<reason>`。 */
export interface LocatorRefusal {
  drawable: false
  reason: string
}

export type LocatorVerdict = LocatorDrawable | LocatorRefusal

/** 本屏唯一接受的坐标系。别的坐标系没有页面图映射，猜一个位置是不允许的。 */
export const PDF_POINTS = 'pdf_points'

/** 拒绝标签前缀：文案固定，测试与人都靠它识别「这里没有画」。 */
export const LOCATOR_REFUSAL_PREFIX = '无法绘制'

/**
 * 角色 → 描边色。同一个事实的多个引用画在同一页上，必须一眼能分出角色：
 * 实体标识（谁）/ 表头（哪个指标）/ 取值（多少）。
 */
export const ROLE_STROKE_COLORS: Record<string, string> = {
  entity: '#2563eb',
  header: '#d97706',
  value: '#dc2626',
  lower: '#dc2626',
  upper: '#dc2626',
  context: '#64748b',
}
/** 未声明角色（或后端将来新增角色）一律灰色：不猜它属于哪一类。 */
export const DEFAULT_STROKE_COLOR = '#64748b'

/** 每条引用的描边色：按 role 取，未知/缺省回落灰色。 */
export function refStrokeColor(role?: string | null): string {
  if (!role) return DEFAULT_STROKE_COLOR
  return ROLE_STROKE_COLORS[role] ?? DEFAULT_STROKE_COLOR
}

/** 拒绝标签的完整文案：`无法绘制：<reason>`。 */
export function locatorRefusalLabel(reason: string): string {
  return `${LOCATOR_REFUSAL_PREFIX}：${reason || '引用信息不足'}`
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** 只接受「1 起的整数页码」；其余（undefined/null/0/负/小数/字符串）都不算有效页。 */
export function validPageNo(value: unknown): number | null {
  if (!isFiniteNumber(value) || !Number.isInteger(value) || value < 1) return null
  return value
}

/**
 * 判定一条证据引用能否绘制。六个必要条件（缺一不可）：
 *   1. bbox 是 4 个有限数字的数组；
 *   2. page_size 是 [宽,高]，且宽 > 0、高 > 0；
 *   3. page_no 是 >= 1 的整数；
 *   4. coordinate_system === 'pdf_points'；
 *   5. bbox 的宽高严格为正（0 面积的框画出来什么也没有，却会让用户以为
 *      「定位成功了但看不见」——那仍然是过度声称）；
 *   6. 页面旋转**没有**被声明为非 0（见下）。
 *
 * WHY 旋转只对「显式声明」拒绝：本屏的 pdf_points 引用来自 ingestion 的
 * `rendered-page-points-top-left` 约定（见 ingestion/pdf_prepare.py 的
 * bbox_coordinate_system），页面图与 bbox 出自同一次渲染，旋转已经烘进两者，
 * 因此「字段缺失」不构成任何未补偿的旋转声明，不该据此拒绝（那会让整屏定位框全灭，
 * 把「不猜」变成「什么都不给」）。
 * 反过来，后端一旦**显式**声明 `rotation_degrees` 不是 360 的整数倍，就说明该页相对
 * 页面图被旋转过：此时 bbox 落在未旋转的坐标系里，直接叠画必然错位。
 * 画错一个框比不画更糟——用户会把猜出来的区域当成证据来源，所以宁可不画。
 */
export function locatorVerdict(ref: EvidenceRefLike | null | undefined): LocatorVerdict {
  if (!ref) return { drawable: false, reason: '引用不存在' }

  const box = ref.bbox
  if (!Array.isArray(box) || box.length !== 4 || !box.every(isFiniteNumber)) {
    return { drawable: false, reason: 'bbox 不是 4 个有限数字' }
  }
  const [x0, y0, x1, y1] = box as number[]

  const size = ref.page_size
  if (!Array.isArray(size) || size.length !== 2 || !size.every(isFiniteNumber)) {
    return { drawable: false, reason: 'page_size 不是 [宽, 高]' }
  }
  const [width, height] = size as number[]
  if (!(width > 0) || !(height > 0)) {
    return { drawable: false, reason: `page_size 宽高必须为正（当前 ${width}×${height}）` }
  }

  const pageNo = validPageNo(ref.page_no)
  if (pageNo === null) {
    return { drawable: false, reason: '页码缺失或不是 >= 1 的整数' }
  }

  if (ref.coordinate_system !== PDF_POINTS) {
    const declared = ref.coordinate_system ? String(ref.coordinate_system) : '未声明'
    return { drawable: false, reason: `坐标系 ${declared} 不是 ${PDF_POINTS}` }
  }

  // 旋转护栏：只对「后端显式声明了旋转」的情况拒绝，缺失不等于 0 也不等于「有旋转」。
  // 声明了却读不出数字 = 结论不可用，同样拒绝（不拿它当 0）。
  if (ref.rotation_degrees !== undefined && ref.rotation_degrees !== null
      && !isFiniteNumber(ref.rotation_degrees)) {
    return { drawable: false, reason: `页面旋转角度无法解析（${String(ref.rotation_degrees)}）` }
  }
  if (isFiniteNumber(ref.rotation_degrees) && ref.rotation_degrees % 360 !== 0) {
    return { drawable: false, reason: `页面被旋转 ${ref.rotation_degrees}°：无法对齐未旋转的页面图` }
  }

  if (!(x1 > x0) || !(y1 > y0)) {
    return { drawable: false, reason: 'bbox 宽或高为 0，没有可绘制区域' }
  }

  return {
    drawable: true,
    pageNo,
    bbox: [x0, y0, x1, y1],
    pageSize: [width, height],
  }
}

/**
 * 一组引用里「可以拿来展示原页」的页码：优先焦点引用的有效页码，否则取第一条
 * 有效页码的引用。全部无效时返回 null —— 此时连页面图都不该显示（没有可信的页）。
 */
export function pageForRefs(refs: EvidenceRefLike[], focusIndex = 0): number | null {
  const focused = validPageNo(refs?.[focusIndex]?.page_no)
  if (focused !== null) return focused
  for (const ref of refs ?? []) {
    const page = validPageNo(ref?.page_no)
    if (page !== null) return page
  }
  return null
}
