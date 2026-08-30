// 工作流节点类型元数据：名称、简约 SVG 图标、主题色
// SVG 使用 currentColor，外层容器通过 color 控制颜色
const svg = (path) => `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`

export const TYPE_META = {
  start: {
    name: '开始',
    color: '#475569',
    icon: svg('<circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/>'),
  },
  end: {
    name: '结束',
    color: '#475569',
    icon: svg('<circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6" rx="1.5"/>'),
  },
  agent: {
    name: '智能体',
    color: '#0891b2',
    icon: svg('<path d="M12 2l8.66 5v10L12 22l-8.66-5V7z"/>'),
  },
  service: {
    name: '实体服务',
    color: '#2563eb',
    icon: svg('<circle cx="12" cy="12" r="4"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1M5.6 18.4l2.1-2.1m8.6-8.6l2.1-2.1"/>'),
  },
  llm: {
    name: '大模型',
    color: '#4f46e5',
    icon: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 12h8M8 15h5"/>'),
  },
  condition: {
    name: '条件分支',
    color: '#b45309',
    icon: svg('<path d="M12 3l9 9-9 9-9-9z"/><path d="M8 12h8"/><path d="M13 9l3 3-3 3"/>'),
  },
  code: {
    name: '代码',
    color: '#059669',
    icon: svg('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="10" y1="21" x2="14" y2="3"/>'),
  },
  human: {
    name: '人工',
    color: '#d97706',
    icon: svg('<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/><polyline points="16 11 17.5 12.5 21 9"/>'),
  },
  http: {
    name: 'HTTP 请求',
    color: '#0ea5e9',
    icon: svg('<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
  },
}
