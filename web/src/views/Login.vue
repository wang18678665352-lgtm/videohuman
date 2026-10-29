<template>
  <div class="login-wrap">
    <div class="glow" />
    <div class="login-card">
      <div class="mark"><el-icon :size="24"><VideoCameraFilled /></el-icon></div>
      <h1>videohuman 工作台</h1>
      <p class="tip">该实例已启用访问令牌,请输入 token 继续</p>
      <el-input v-model="token" size="large" placeholder="访问 token" show-password :prefix-icon="Key"
        @keyup.enter="submit" />
      <el-button class="submit" type="primary" size="large" :loading="checking" @click="submit">进入工作台</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Key, VideoCameraFilled } from '@element-plus/icons-vue'
import { getSettings, setToken } from '../api.js'

const route = useRoute()
const router = useRouter()
const token = ref('')
const checking = ref(false)

async function submit() {
  if (!token.value.trim()) return ElMessage.warning('请输入 token')
  checking.value = true
  try {
    setToken(token.value.trim())
    await getSettings() // 验证 token 有效性(失败会被拦截器清掉并 401)
    router.replace(typeof route.query.back === 'string' ? route.query.back : '/')
  } catch {
    ElMessage.error('token 错误')
  } finally {
    checking.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  position: relative; min-height: 100vh; display: grid; place-items: center; padding: 16px; overflow: hidden;
  background: #0f1020;
}
.glow {
  position: absolute; inset: -20%;
  background:
    radial-gradient(40% 40% at 30% 30%, rgba(99, 102, 241, 0.45), transparent 70%),
    radial-gradient(35% 35% at 70% 70%, rgba(168, 85, 247, 0.35), transparent 70%);
  filter: blur(20px);
}
.login-card {
  position: relative; width: 100%; max-width: 380px; padding: 40px 32px 32px; text-align: center;
  background: rgba(255, 255, 255, 0.97); border-radius: 20px; box-shadow: 0 30px 80px rgba(0, 0, 0, 0.35);
}
.mark {
  width: 52px; height: 52px; margin: 0 auto 18px; border-radius: 14px; display: grid; place-items: center; color: #fff;
  background: linear-gradient(135deg, #6366f1, #a855f7); box-shadow: 0 10px 24px rgba(124, 77, 255, 0.4);
}
h1 { margin: 0; font-size: 22px; }
.tip { color: var(--vh-text-3); font-size: 13.5px; margin: 8px 0 24px; }
.submit { width: 100%; margin-top: 14px; }
</style>
