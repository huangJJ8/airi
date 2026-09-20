<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { describeApiError } from '../api/client'
import {
  compareVersions,
  fetchActiveVersion,
  fetchMetricDefinitions,
  fetchReleaseEvents,
  fetchVersion,
  fetchVersions,
} from '../api/registry'
import type {
  MetricDefinition,
  MetricReleaseEvent,
  MetricVersion,
  MetricVersionDiff,
} from '../types/airi'
import StatusTag from '../components/StatusTag.vue'
import { formatDateTime } from '../utils/format'

const loading = ref(true)
const error = ref('')
const definitions = ref<MetricDefinition[]>([])
const selectedKey = ref('')
const versions = ref<MetricVersion[]>([])
const active = ref<MetricVersion | null>(null)
const events = ref<MetricReleaseEvent[]>([])

const selectedVersion = ref<MetricVersion | null>(null)
const diff = ref<MetricVersionDiff | null>(null)
const diffLoading = ref(false)
const fromVersion = ref('')
const toVersion = ref('')

const sortedVersions = computed(() =>
  [...versions.value].sort((a, b) => b.created_at.localeCompare(a.created_at)),
)

const classificationTone = computed(() => {
  switch (diff.value?.classification) {
    case 'major':
      return 'danger'
    case 'minor':
      return 'warning'
    case 'patch':
      return 'info'
    default:
      return 'info'
  }
})

onMounted(async () => {
  try {
    definitions.value = await fetchMetricDefinitions()
    if (definitions.value.length > 0) {
      selectedKey.value = definitions.value[0].metric_key
      await loadFamily(selectedKey.value)
    }
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    loading.value = false
  }
})

async function loadFamily(metricKey: string) {
  loading.value = true
  error.value = ''
  selectedVersion.value = null
  diff.value = null
  try {
    const [list, activeVersion, releaseEvents] = await Promise.all([
      fetchVersions(metricKey),
      fetchActiveVersionSafe(metricKey),
      fetchReleaseEvents(metricKey).catch(() => [] as MetricReleaseEvent[]),
    ])
    versions.value = list
    active.value = activeVersion
    events.value = releaseEvents
    if (list.length > 0) await selectVersion(list[0])
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    loading.value = false
  }
}

async function fetchActiveVersionSafe(metricKey: string) {
  try {
    return await fetchActiveVersion(metricKey)
  } catch {
    return null
  }
}

async function selectVersion(version: MetricVersion) {
  error.value = ''
  try {
    selectedVersion.value = await fetchVersion(version.metric_key, version.version)
  } catch (e) {
    error.value = describeApiError(e)
  }
}

async function runCompare() {
  if (!fromVersion.value || !toVersion.value || fromVersion.value === toVersion.value) return
  diffLoading.value = true
  error.value = ''
  try {
    diff.value = await compareVersions(selectedKey.value, fromVersion.value, toVersion.value)
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    diffLoading.value = false
  }
}
</script>

<template>
  <div class="page" v-loading="loading">
    <h1 class="page-title">Metric Registry</h1>
    <p class="page-subtitle">
      Every promoted metric is versioned, reviewed and released through governed events — nothing here was
      written by the UI.
    </p>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="section" />

    <template v-if="definitions.length > 0">
      <el-card shadow="never" class="section">
        <div class="family-row">
          <span class="family-label">Metric Family</span>
          <el-select v-model="selectedKey" style="width: 280px" @change="loadFamily">
            <el-option
              v-for="definition in definitions"
              :key="definition.metric_key"
              :value="definition.metric_key"
              :label="`${definition.metric_key} — ${definition.display_name}`"
            />
          </el-select>
          <StatusTag v-if="active" :status="active.status" />
          <span v-if="active" class="mono active-name">{{ active.metric_name }}</span>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Version Timeline</span></template>
        <el-empty v-if="sortedVersions.length === 0" description="No versions registered yet" :image-size="70" />
        <div
          v-for="version in sortedVersions"
          :key="version.metric_version_id"
          class="version-row"
          role="button"
          tabindex="0"
          :class="{ selected: selectedVersion?.version === version.version }"
          @click="selectVersion(version)"
          @keyup.enter="selectVersion(version)"
        >
          <div class="version-main">
            <span class="version-num mono">{{ version.version }}</span>
            <span class="version-name mono">{{ version.metric_name }}</span>
            <StatusTag :status="version.status" />
            <el-tag v-if="version.synthetic_data" size="small" type="warning" effect="plain">synthetic</el-tag>
          </div>
          <div class="version-meta">
            created {{ formatDateTime(version.created_at) }}
            <span v-if="version.version_change" class="mono"> · {{ version.version_change.kind }} from {{ version.version_change.previous_version }}</span>
          </div>
        </div>
      </el-card>

      <el-card v-if="selectedVersion" shadow="never" class="section">
        <template #header>
          <span class="card-title">Version Detail — {{ selectedVersion.metric_name }} {{ selectedVersion.version }}</span>
        </template>
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="Version"><span class="mono">{{ selectedVersion.version }}</span></el-descriptions-item>
          <el-descriptions-item label="Status"><StatusTag :status="selectedVersion.status" /></el-descriptions-item>
          <el-descriptions-item label="Created">{{ formatDateTime(selectedVersion.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="Metric name"><span class="mono">{{ selectedVersion.metric_name }}</span></el-descriptions-item>
          <el-descriptions-item label="Source artifact"><span class="mono">{{ selectedVersion.artifact_id.slice(0, 18) }}…</span></el-descriptions-item>
          <el-descriptions-item label="Promotion review"><span class="mono">{{ selectedVersion.source_promotion_review_id?.slice(0, 18) ?? '—' }}…</span></el-descriptions-item>
          <el-descriptions-item label="IR hash" :span="3">
            <span class="mono">{{ selectedVersion.metric_ir_hash }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Version Comparison</span></template>
        <div class="compare-row">
          <el-select v-model="fromVersion" placeholder="from version" style="width: 160px">
            <el-option v-for="version in sortedVersions" :key="version.version" :value="version.version" :label="version.version" />
          </el-select>
          <span class="compare-arrow">→</span>
          <el-select v-model="toVersion" placeholder="to version" style="width: 160px">
            <el-option v-for="version in sortedVersions" :key="version.version" :value="version.version" :label="version.version" />
          </el-select>
          <el-button type="primary" :loading="diffLoading" @click="runCompare">Compare</el-button>
          <el-tag v-if="diff" :type="classificationTone" effect="light">{{ diff.classification.toUpperCase() }}</el-tag>
        </div>
        <el-descriptions v-if="diff" :column="1" size="small" border class="diff-block">
          <el-descriptions-item v-for="field in diff.changed_fields" :key="field.field" :label="field.field">
            <span class="mono">{{ JSON.stringify(field.before) }}</span>
            <span class="diff-arrow"> ↓ </span>
            <span class="mono diff-after">{{ JSON.stringify(field.after) }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div v-if="diff" class="unchanged mono">{{ diff.unchanged.length }} field(s) unchanged</div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Release Events</span></template>
        <el-empty v-if="events.length === 0" description="No release events" :image-size="70" />
        <el-timeline v-else class="events">
          <el-timeline-item
            v-for="event in events"
            :key="event.event_id"
            :timestamp="formatDateTime(event.created_at)"
            placement="top"
            :type="event.event_type.includes('rollback') ? 'warning' : 'primary'"
          >
            <span class="event-type mono">{{ event.event_type }}</span>
            <span class="event-actor">by {{ event.actor }}</span>
          </el-timeline-item>
        </el-timeline>
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="No metric families — run scripts/seed_demo.py to seed the demo registry" />
  </div>
</template>

<style scoped>
.section {
  margin-bottom: 16px;
}

.card-title {
  font-weight: 650;
}

.family-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.family-label {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.active-name {
  color: var(--airi-text-secondary);
}

.version-row {
  border: 1px solid var(--airi-border);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 10px;
  cursor: pointer;
}

.version-row.selected {
  border-color: var(--airi-primary);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
}

.version-main {
  display: flex;
  align-items: center;
  gap: 12px;
}

.version-num {
  font-weight: 700;
  font-size: 14px;
}

.version-name {
  color: var(--airi-text-secondary);
}

.version-meta {
  color: var(--airi-text-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.compare-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.compare-arrow {
  color: var(--airi-text-secondary);
}

.diff-block {
  margin-top: 4px;
}

.diff-arrow {
  color: var(--airi-text-secondary);
  margin: 0 8px;
}

.diff-after {
  font-weight: 700;
}

.unchanged {
  color: var(--airi-text-secondary);
  margin-top: 8px;
  font-size: 12px;
}

.events {
  padding-left: 4px;
}

.event-type {
  font-weight: 650;
  margin-right: 10px;
}

.event-actor {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}
</style>
