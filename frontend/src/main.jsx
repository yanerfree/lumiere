import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCNRaw from 'antd/locale/zh_CN'
import enUSRaw from 'antd/locale/en_US'
import App from './App.jsx'
import { LangProvider, useLang } from './utils/i18n.jsx'
// 等宽字体自带，不指望使用者的系统里装了什么：实测常见等宽字体（JetBrains Mono /
// Fira Code / Consolas / DejaVu Sans Mono…）一个都没有时，monospace 会落到
// 文泉驿正黑等宽这类中文字体上，渲染 ASCII 笔画粗细不均、看久了眼花。
// 只要 latin 子集（等宽文本都是 URL / JSON / 变量名，用不到中文），两个字重共 43KB。
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-500.css'
import './styles/global.css'

// antd/locale/* 是 CJS 转发（module.exports = require('../lib/locale/xx')），
// 而 lib 下是 Babel 输出的 exports.default，打包后 default import 拿到的是
// { default: {...} } 这层壳，而不是语言包本身。直接传给 ConfigProvider 会
// 静默退回英文（分页显示 "/ page"、Popconfirm 显示 OK/Cancel）。这里手动解包。
const zhCN = zhCNRaw?.default ?? zhCNRaw
const enUS = enUSRaw?.default ?? enUSRaw

const theme = {
  token: {
    colorPrimary: '#0ea5a0',
    // 基准字号跟 global.css 的 --fs-base 对齐。之前 antd 用 14、CSS 用 13，
    // 结果"没显式写字号的地方"比邻居大一号，全站字号看着乱。
    fontSize: 13,
    borderRadius: 12,
    borderRadiusLG: 16,
    borderRadiusSM: 8,
    colorBgContainer: 'rgba(255,255,255,0.55)',
    fontFamily: "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif",
    colorText: '#1d2129',
    colorTextSecondary: '#86909c',
    colorBorder: 'rgba(0,0,0,0.06)',
    colorBorderSecondary: 'rgba(0,0,0,0.04)',
    controlHeight: 32,
    colorSuccess: '#0ea5a0',
    colorError: '#e8453c',
    colorWarning: '#f0a020',
    colorInfo: '#4e8af0',
    colorBgElevated: 'rgba(255,255,255,0.95)',
  },
  components: {
    Button: {
      borderRadius: 20,
      borderRadiusLG: 20,
      borderRadiusSM: 12,
      primaryShadow: '0 2px 8px rgba(14,165,160,0.25)',
      fontWeight: 500,
    },
    Card: {
      borderRadiusLG: 16,
      colorBgContainer: 'rgba(255,255,255,0.55)',
      boxShadowTertiary: 'none',
    },
    Table: {
      borderRadiusLG: 16,
      colorBgContainer: 'transparent',
      headerBg: 'rgba(255,255,255,0.45)',
      rowHoverBg: 'rgba(255,255,255,0.6)',
    },
    Tag: {
      borderRadiusSM: 12,
    },
    Modal: {
      borderRadiusLG: 16,
    },
    Input: {
      borderRadius: 8,
      borderRadiusLG: 10,
    },
    Select: {
      borderRadius: 8,
    },
    Message: {
      borderRadiusLG: 20,
    },
    Menu: {
      itemBorderRadius: 12,
      itemSelectedBg: 'rgba(14,165,160,0.1)',
      itemSelectedColor: '#0ea5a0',
      itemHoverBg: 'rgba(14,165,160,0.05)',
      itemHoverColor: '#0ea5a0',
      subMenuItemBg: 'transparent',
    },
  },
}

function Root() {
  const { lang } = useLang()
  return (
    <ConfigProvider locale={lang === 'en' ? enUS : zhCN} theme={theme}>
      <App />
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <LangProvider>
      <Root />
    </LangProvider>
  </BrowserRouter>,
)
