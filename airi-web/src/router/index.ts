import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('../pages/DashboardPage.vue') },
    {
      path: '/development',
      name: 'development',
      component: () => import('../pages/DevelopmentPage.vue'),
    },
    { path: '/testing', name: 'testing', component: () => import('../pages/TestingPage.vue') },
    {
      path: '/experiments',
      name: 'experiments',
      component: () => import('../pages/ExperimentPage.vue'),
    },
    {
      path: '/reflection',
      name: 'reflection',
      component: () => import('../pages/ReflectionPage.vue'),
    },
    { path: '/registry', name: 'registry', component: () => import('../pages/RegistryPage.vue') },
  ],
})

export default router
