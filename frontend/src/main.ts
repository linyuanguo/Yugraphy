import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import AppCopyright from './components/AppCopyright.vue'
import router from './router'
import { useAuthStore } from '@/stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 恢复登录态：仅会话级（关浏览器即失效）或「记住我」未过期时有效
useAuthStore(pinia).restore()

app.use(router)
app.use(Antd)
app.component('AppCopyright', AppCopyright)
app.mount('#app')
