<script setup lang="ts">
import { computed } from 'vue'
import { statusDisplay } from '../utils/status'

const props = defineProps<{ status: string | null | undefined }>()

const display = computed(() => statusDisplay(props.status))
</script>

<template>
  <el-tag :type="display.tone" effect="light" size="small">{{ display.label }}</el-tag>
</template>
