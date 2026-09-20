<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps<{ code: string; title?: string }>()

const copied = ref(false)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.code)
    copied.value = true
    ElMessage.success('SQL copied')
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    ElMessage.error('Copy failed')
  }
}
</script>

<template>
  <div>
    <div class="sql-header">
      <span class="sql-title">{{ props.title ?? 'SQL Preview' }}</span>
      <span class="sql-note">Deterministic output — generated from the Metric IR, never by the LLM directly</span>
      <el-button size="small" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</el-button>
    </div>
    <pre class="sql-block mono">{{ code }}</pre>
  </div>
</template>

<style scoped>
.sql-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.sql-title {
  font-weight: 650;
}

.sql-note {
  flex: 1;
  color: var(--airi-text-secondary);
  font-size: 12px;
}
</style>
