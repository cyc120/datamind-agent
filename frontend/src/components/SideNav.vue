<script setup lang="ts">
import { useRouter } from 'vue-router'

defineProps<{
  health: 'checking' | 'ok' | 'down'
  model: 'checking' | 'ok' | 'down'
}>()

const router = useRouter()

const items = [
  { key: 'chat', label: '问数与问答', icon: '◎', active: true },
  { key: 'runs', label: '运行记录', icon: '↺', active: false },
  { key: 'model', label: '模型接入', icon: '⚙', active: false },
]
</script>

<template>
  <aside class="sidenav">
    <div class="brand" @click="router.push('/')">
      <span class="logo">DM</span>
      <div>
        <strong>DataMind</strong>
        <small>可解释精准问数</small>
      </div>
    </div>

    <nav class="nav">
      <button
        v-for="it in items"
        :key="it.key"
        class="nav-item"
        :class="{ active: it.active }"
        :disabled="!it.active"
      >
        <span class="ico">{{ it.icon }}</span>{{ it.label }}
        <span v-if="!it.active" class="soon">即将</span>
      </button>
    </nav>

    <div class="status">
      <div class="row">
        <span class="dot" :class="health"></span>
        <span>服务</span>
        <span class="val">{{ health === 'ok' ? '在线' : health === 'down' ? '离线' : '检测中' }}</span>
      </div>
      <div class="row">
        <span class="dot" :class="model"></span>
        <span>模型</span>
        <span class="val">{{ model === 'ok' ? '已接入' : model === 'down' ? '未接入' : '检测中' }}</span>
      </div>
      <p class="note">密钥存于后端元数据库，页面只显示状态。</p>
    </div>
  </aside>
</template>

<style scoped>
.sidenav {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-line);
  background: rgba(10, 14, 24, 0.75);
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  padding: 18px 12px;
  gap: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  cursor: pointer;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-weight: 700;
  color: #04101d;
  background: linear-gradient(135deg, #7dd3fc, #818cf8);
  font-size: 15px;
}
.brand strong {
  display: block;
  font-size: 14px;
  color: #e5e9f0;
}
.brand small {
  display: block;
  font-size: 11px;
  color: #64748b;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 10px;
  font-size: 13.5px;
  color: #94a3b8;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.nav-item:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.04);
  color: #e5e9f0;
}
.nav-item.active {
  background: linear-gradient(90deg, rgba(56, 189, 248, 0.14), rgba(129, 140, 248, 0.08));
  color: #e0f2fe;
}
.nav-item:disabled {
  cursor: default;
  opacity: 0.55;
}
.ico {
  width: 18px;
  text-align: center;
  opacity: 0.8;
}
.soon {
  margin-left: auto;
  font-size: 10px;
  color: #64748b;
  border: 1px solid var(--color-line);
  border-radius: 6px;
  padding: 1px 6px;
}
.status {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 8px;
  border-top: 1px solid var(--color-line);
  font-size: 12px;
  color: #94a3b8;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row .val {
  margin-left: auto;
  color: #cbd5e1;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #64748b;
}
.dot.ok {
  background: #4ade80;
  box-shadow: 0 0 8px rgba(74, 222, 128, 0.7);
}
.dot.down {
  background: #fb7185;
  box-shadow: 0 0 8px rgba(251, 113, 133, 0.7);
}
.dot.checking {
  background: #fbbf24;
  animation: breathe 1.6s ease-in-out infinite;
}
.note {
  font-size: 11px;
  color: #475569;
  line-height: 1.5;
  margin: 4px 0 0;
}
</style>
