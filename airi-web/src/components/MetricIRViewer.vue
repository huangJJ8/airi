<script setup lang="ts">
import { computed } from 'vue'
import type { MetricIR } from '../types/airi'
import { relationshipRows } from '../utils/metricIr'

const props = defineProps<{ ir: MetricIR }>()

/** Friendly summary first; the raw structured JSON stays one click away. */
const summary = computed(() => {
  const ir = props.ir
  if (ir.metric_type === 'derived') {
    return [
      { label: 'Metric Type', value: 'derived (growth_rate)' },
      { label: 'Name', value: String(ir.name ?? '') },
      { label: 'Display Name', value: String(ir.display_name ?? '—') },
      { label: 'Description', value: String(ir.description ?? '—') },
    ]
  }
  const base = [
    { label: 'Metric Name', value: String(ir.name ?? '—') },
    { label: 'Display Name', value: String(ir.display_name ?? '—') },
    { label: 'Description', value: String(ir.description ?? '—') },
    { label: 'Entity', value: `${ir.entity_type ?? '—'} · key = ${ir.entity_key}` },
    {
      label: 'Aggregation',
      value: `${ir.aggregation.function.toUpperCase()}${ir.aggregation.field ? `(${ir.aggregation.field})` : ''}`,
    },
    { label: 'Source', value: `${ir.source.database}.${ir.source.table}` },
    {
      label: 'Window',
      value: ir.window
        ? `last ${ir.window.size} ${ir.window.unit}s on ${ir.window.time_field} (${ir.window.timezone})`
        : '— (state of record)',
    },
  ]
  // A relationship metric adds its declared join path and exclusions.
  return [...base, ...relationshipRows(ir)]
})

const pretty = computed(() => JSON.stringify(props.ir, null, 2))
</script>

<template>
  <div>
    <el-descriptions :column="2" size="small" border>
      <el-descriptions-item v-for="item in summary" :key="item.label" :label="item.label" label-width="130px">
        <span class="mono">{{ item.value }}</span>
      </el-descriptions-item>
    </el-descriptions>
    <el-collapse class="ir-json">
      <el-collapse-item title="Metric IR (structured JSON)" name="json">
        <pre class="mono ir-pre">{{ pretty }}</pre>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.ir-json {
  margin-top: 12px;
  border-top: none;
}

.ir-pre {
  background: #f8fafc;
  border: 1px solid var(--airi-border);
  border-radius: 6px;
  padding: 12px;
  overflow: auto;
  max-height: 320px;
}
</style>
