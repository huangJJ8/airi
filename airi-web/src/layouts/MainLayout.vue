<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { DEMO_STEPS, demoState } from '../composables/demo'

const route = useRoute()

const navs = [
  { path: '/dashboard', label: 'Dashboard', icon: 'Odometer' },
  { path: '/development', label: 'Metric Development', icon: 'EditPen' },
  { path: '/testing', label: 'Metric Testing', icon: 'Finished' },
  { path: '/experiments', label: 'Experiment', icon: 'DataAnalysis' },
  { path: '/reflection', label: 'Reflection & Refinement', icon: 'MagicStick' },
  { path: '/registry', label: 'Metric Registry', icon: 'CollectionTag' },
]

const activeStep = computed(() => {
  switch (route.name) {
    case 'testing':
      return 1
    case 'experiments':
      return 2
    case 'reflection':
      return 3
    case 'registry':
      return 4
    default:
      return 0
  }
})

const collapsed = ref(false)
</script>

<template>
  <el-container class="shell">
    <el-header class="topbar" height="52px">
      <div class="brand">
        <span class="logo">AIRI</span>
        <span class="brand-sub">Risk Indicator Research Agent</span>
      </div>
      <el-tag type="warning" effect="plain" size="small">LOCAL DEMO · Synthetic Data</el-tag>
      <el-tag v-if="demoState.currentStep > 0" type="info" effect="plain" size="small" class="run-tag">
        Guided run active
      </el-tag>
    </el-header>

    <el-container class="body">
      <el-aside :width="collapsed ? '64px' : '220px'" class="sidebar">
        <el-menu :default-active="route.path" router :collapse="collapsed" class="menu">
          <el-menu-item v-for="item in navs" :key="item.path" :index="item.path">
            <span>{{ item.label }}</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <el-container>
        <div class="stepper">
          <div class="stepper-inner">
            <template v-for="(step, index) in DEMO_STEPS" :key="step">
              <div class="step" :class="{ done: index < activeStep, current: index === activeStep }">
                <span class="dot">{{ index < activeStep ? '✓' : index + 1 }}</span>
                <span class="label">{{ step }}</span>
              </div>
              <div v-if="index < DEMO_STEPS.length - 1" class="bar" :class="{ done: index < activeStep - 0 }" />
            </template>
          </div>
        </div>
        <el-main class="main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  height: 100vh;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--airi-surface);
  border-bottom: 1px solid var(--airi-border);
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: 1;
}

.logo {
  font-size: 18px;
  font-weight: 800;
  color: var(--airi-primary);
  letter-spacing: 1px;
}

.brand-sub {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.run-tag {
  margin-left: 8px;
}

.body {
  min-height: 0;
}

.sidebar {
  background: var(--airi-surface);
  border-right: 1px solid var(--airi-border);
  transition: width 0.2s;
}

.menu {
  border-right: none;
}

.stepper {
  background: var(--airi-surface);
  border-bottom: 1px solid var(--airi-border);
  padding: 10px 24px;
}

.stepper-inner {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-size: 13px;
  color: var(--airi-text-secondary, #6b7280);
}

.step .dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--airi-border, #d0d3d9);
  font-size: 11px;
  line-height: 1;
}

.step.current {
  color: var(--airi-primary, #1f6feb);
  font-weight: 600;
}

.step.current .dot {
  border-color: var(--airi-primary, #1f6feb);
  color: var(--airi-primary, #1f6feb);
}

.step.done {
  color: var(--airi-success, #1a7f37);
}

.step.done .dot {
  border-color: var(--airi-success, #1a7f37);
  color: var(--airi-success, #1a7f37);
}

.bar {
  width: 34px;
  height: 1px;
  background: var(--airi-border, #d0d3d9);
}

.bar.done {
  background: var(--airi-success, #1a7f37);
}

.main {
  padding: 0;
  overflow: auto;
}
</style>
