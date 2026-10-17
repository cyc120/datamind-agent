<script setup lang="ts">
import { computed } from 'vue'
import { drawingFileUrl, drawingPreviewUrl, drawingViewerUrl } from '../../api/client'

/**
 * 嵌入式图纸查看器 + 原始文件 / SVG 预览链接。
 *
 * WHY 三件事必须同时说清楚：
 *   1. iframe 的 sandbox 是**服务端**决定的（viewer 响应头 CSP
 *      `sandbox allow-scripts; default-src 'none'; ...`，**故意不含** allow-same-origin）。
 *      客户端绝不能自己补 allow-same-origin —— 那会把服务端刻意隔离的图纸脚本
 *      放回同源上下文里。这里的 sandbox 属性只是把同一策略在客户端再声明一次。
 *   2. 查看器/预览都是服务端本地渲染（ingestion/cad_render），只证明「本地解析出的
 *      几何画得出来」，不是图纸审批、也不是生产合格结论。
 *   3. 内部 object_uri **不渲染**：读取路径由 source_sha256 推出，存储位置不是
 *      界面信息（client.ts 也没导出它）。
 */
const props = defineProps<{
  documentId: string
  /** 明确指定版本：省略 = 读当前版本，但本页始终显式带上，避免「看的是哪一版」含糊 */
  version: number | null
  versionLabel: string
}>()

const viewerSrc = computed(() => drawingViewerUrl(props.documentId, props.version))
const fileHref = computed(() => drawingFileUrl(props.documentId, props.version))
const previewHref = computed(() => drawingPreviewUrl(props.documentId, props.version))
</script>

<template>
  <section class="cad-section" data-testid="cad-viewer">
    <h2 class="cad-sec-title">图纸查看器<span class="cad-sec-tag">{{ versionLabel }}</span></h2>
    <p class="cad-hint">
      图纸在隔离的沙箱里渲染：只能执行脚本，拿不到本站的登录态与数据。这一项由服务端的安全策略决定，
      客户端不会去放开它。
    </p>
    <p class="cad-hint">
      这里显示的是服务端本地渲染的结果，只证明「本地解析出的几何画得出来」，
      不构成图纸审批、生产绑定或质量结论。
    </p>

    <div class="cad-viewer-frame">
      <iframe
        :src="viewerSrc"
        sandbox="allow-scripts"
        referrerpolicy="no-referrer"
        loading="lazy"
        title="图纸查看器（服务端渲染）"
        data-testid="cad-viewer-frame"
      ></iframe>
    </div>

    <p class="cad-links">
      <a :href="fileHref" data-testid="cad-file-link" rel="noopener">下载原始 DXF</a>
      <a :href="previewHref" target="_blank" rel="noopener" data-testid="cad-preview-link">
        打开 SVG 预览
      </a>
      <span class="cad-links-note">两个链接都指向本站；图纸在服务端的存储位置不下发，本页也不显示。</span>
    </p>
  </section>
</template>

<style scoped>
.cad-section {
  margin: 0 0 22px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
}
.cad-sec-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
  display: flex;
  align-items: baseline;
  gap: 10px;
}
/* 节标题旁的标签：蓝底白字加粗椭圆（原来是灰字描边） */
.cad-sec-tag {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  padding: 2px 9px;
}
.cad-hint {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.cad-hint code,
.cad-links code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
}
.cad-viewer-frame {
  margin: 10px 0 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  overflow: hidden;
  background: var(--color-ink);
}
.cad-viewer-frame iframe {
  display: block;
  width: 100%;
  height: 460px;
  border: 0;
}
.cad-links {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: baseline;
  margin: 0;
  font-size: 14px;
}
.cad-links a {
  color: var(--color-accent);
  text-decoration: none;
  border-bottom: 1px dashed rgba(37, 99, 235, 0.5);
}
.cad-links-note {
  font-size: 13px;
  color: var(--color-text);
}
</style>
