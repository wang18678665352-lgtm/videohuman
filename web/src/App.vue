<template>
  <el-container class="layout">
    <el-header class="header">
      <span class="logo" @click="$router.push('/')">videohuman 工作台</span>
      <el-menu mode="horizontal" :default-active="activeMenu" router class="nav">
        <el-menu-item index="/">任务</el-menu-item>
        <el-menu-item index="/workflows">工作流</el-menu-item>
        <el-menu-item index="/new">数字人快捷任务</el-menu-item>
        <el-menu-item index="/assets">素材与设置</el-menu-item>
      </el-menu>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
// 工作流的编辑/运行子页面也高亮"工作流"菜单
const activeMenu = computed(() => (route.path.startsWith('/workflows') ? '/workflows' : route.path))
</script>

<style>
body { margin: 0; background: #f5f7fa; }
.layout .header {
  display: flex; align-items: center; gap: 32px;
  background: #fff; border-bottom: 1px solid #e4e7ed;
}
.logo { font-size: 18px; font-weight: 700; cursor: pointer; white-space: nowrap; }
.nav { flex: 1; border-bottom: none; }
</style>
