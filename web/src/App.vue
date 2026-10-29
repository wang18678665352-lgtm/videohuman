<template>
  <router-view v-if="bare" />
  <div v-else class="shell" :class="{ 'nav-open': navOpen }">
    <aside class="sidebar">
      <div class="brand" @click="go('/')">
        <div class="brand-mark"><el-icon :size="18"><VideoCameraFilled /></el-icon></div>
        <div>
          <div class="brand-name">videohuman</div>
          <div class="brand-sub">数字人视频工作台</div>
        </div>
      </div>

      <el-button class="create-btn" type="primary" @click="go('/new')">
        <el-icon><Plus /></el-icon><span>新建数字人任务</span>
      </el-button>

      <nav class="nav">
        <div class="nav-group">工作台</div>
        <a v-for="m in menus" :key="m.path" class="nav-item" :class="{ active: activeMenu === m.path }"
          @click="go(m.path)">
          <el-icon :size="17"><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </a>
      </nav>

      <div class="side-foot">
        <span class="dot" />本地服务 · 在线
      </div>
    </aside>

    <div class="mask" @click="navOpen = false" />

    <div class="content">
      <header class="topbar">
        <el-button class="menu-btn" text @click="navOpen = !navOpen"><el-icon :size="20"><Menu /></el-icon></el-button>
        <div class="crumb">
          <span class="muted">videohuman</span>
          <el-icon class="muted"><ArrowRight /></el-icon>
          <span>{{ currentLabel }}</span>
        </div>
      </header>
      <main class="main" :class="{ full: route.path.endsWith('/edit') }">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowRight, Collection, Files, Menu, MagicStick, Plus, Share, VideoCameraFilled,
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const navOpen = ref(false)

const menus = [
  { path: '/', label: '任务中心', icon: Files },
  { path: '/workflows', label: '工作流', icon: Share },
  { path: '/new', label: '数字人快捷任务', icon: MagicStick },
  { path: '/assets', label: '素材与设置', icon: Collection },
]

// 登录页不套侧边栏
const bare = computed(() => route.path === '/login')
// 工作流的编辑/运行子页面也高亮"工作流"菜单;任务详情高亮"任务中心"
const activeMenu = computed(() => {
  if (route.path.startsWith('/workflows')) return '/workflows'
  if (route.path.startsWith('/jobs')) return '/'
  return route.path
})
const currentLabel = computed(() => {
  if (route.path.startsWith('/jobs/')) return '任务详情'
  if (route.path.endsWith('/edit')) return '工作流编辑器'
  if (route.path.endsWith('/run')) return '运行工作流'
  return menus.find((m) => m.path === activeMenu.value)?.label || ''
})

function go(path) {
  navOpen.value = false
  router.push(path)
}
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; }

/* ---------- 侧边栏 ---------- */
.sidebar {
  position: fixed; inset: 0 auto 0 0; width: 240px; z-index: 30;
  display: flex; flex-direction: column; padding: 20px 14px;
  background: var(--vh-sidebar);
  background-image: radial-gradient(120% 60% at 0% 0%, rgba(99, 102, 241, 0.22), transparent 60%);
  color: #c9cbe0;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 4px 8px 22px; cursor: pointer; }
.brand-mark {
  width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; color: #fff;
  background: linear-gradient(135deg, #6366f1, #a855f7); box-shadow: 0 6px 18px rgba(124, 77, 255, 0.45);
}
.brand-name { color: #fff; font-weight: 700; font-size: 16px; letter-spacing: 0.01em; }
.brand-sub { font-size: 11.5px; color: #8b8ea8; margin-top: 2px; }

.create-btn { width: 100%; height: 40px; border-radius: 10px; margin-bottom: 22px; }
.create-btn span { margin-left: 6px; }

.nav { flex: 1; }
.nav-group { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #6c6f8a; padding: 0 10px 8px; }
.nav-item {
  display: flex; align-items: center; gap: 12px; padding: 10px 12px; margin-bottom: 2px;
  border-radius: 9px; cursor: pointer; font-size: 14px; transition: background 0.15s, color 0.15s;
  user-select: none;
}
.nav-item:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
.nav-item.active { background: rgba(124, 124, 255, 0.16); color: #fff; font-weight: 600; box-shadow: inset 3px 0 0 #8b8bff; }

.side-foot { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #6c6f8a; padding: 0 10px; }
.side-foot .dot { width: 7px; height: 7px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18); }

/* ---------- 内容区 ---------- */
.content { flex: 1; margin-left: 240px; min-width: 0; display: flex; flex-direction: column; }
.topbar {
  position: sticky; top: 0; z-index: 20; height: 56px; display: flex; align-items: center; gap: 8px;
  padding: 0 32px; background: rgba(244, 245, 250, 0.82); backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--vh-border);
}
.crumb { display: flex; align-items: center; gap: 6px; font-size: 13.5px; font-weight: 500; }
.menu-btn { display: none; }
.main { padding: 28px 32px 48px; flex: 1; }
.main.full { padding: 16px 20px; }

.mask { display: none; }

@media (max-width: 960px) {
  .sidebar { transform: translateX(-100%); transition: transform 0.2s ease; }
  .nav-open .sidebar { transform: none; }
  .nav-open .mask { display: block; position: fixed; inset: 0; z-index: 25; background: rgba(10, 10, 25, 0.4); }
  .content { margin-left: 0; }
  .menu-btn { display: inline-flex; }
  .topbar { padding: 0 16px; }
  .main { padding: 20px 16px 40px; }
}
</style>
