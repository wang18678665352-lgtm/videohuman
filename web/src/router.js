import { createRouter, createWebHistory } from 'vue-router'
import JobList from './views/JobList.vue'

// 首页同步加载,其余页面按需加载,减小首屏包体
export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: JobList },
    { path: '/new', component: () => import('./views/JobNew.vue') },
    { path: '/jobs/:id', component: () => import('./views/JobDetail.vue'), props: true },
    { path: '/assets', component: () => import('./views/Assets.vue') },
    { path: '/workflows', component: () => import('./views/Workflows.vue') },
    { path: '/workflows/:id/edit', component: () => import('./views/WorkflowEditor.vue'), props: true },
    { path: '/workflows/:id/run', component: () => import('./views/WorkflowRun.vue'), props: true },
    { path: '/login', component: () => import('./views/Login.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
