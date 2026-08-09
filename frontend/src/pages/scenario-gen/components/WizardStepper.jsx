import { Steps } from 'antd'

const STAGES = [
  { key: 'input', title: '输入需求' },
  { key: 'requirements', title: '确认需求点' },
  { key: 'model', title: '确认场景模型' },
  { key: 'generate', title: '生成' },
  { key: 'review', title: '评审' },
]

const STAGE_ORDER = { input: 0, requirements: 1, model: 2, generate: 3, review: 4 }

function getReachedIndex(taskStatus, hasModel) {
  const base = {
    extracting: 0,
    confirmed: 2,
    generating: 3,
    completed: 4,
    partial_failed: 3,
    failed: 3,
    aborted: 3,
  }
  if (taskStatus === 'model_ready') {
    return hasModel ? 2 : 1
  }
  return base[taskStatus] ?? 0
}

export default function WizardStepper({ currentStage, onStageClick, taskStatus, hasModel }) {
  const currentIndex = STAGE_ORDER[currentStage] ?? 0
  const reachedIndex = getReachedIndex(taskStatus, hasModel)

  // 切步骤走 Steps 的 onChange —— antd v5 的 items **不支持 onClick**，
  // 原来给每个 item 挂 onClick，点一下直接抛 "onClick is not a function"，
  // 步骤条也跳不过去。想回上一步看看（很自然的操作）就撞上。
  return (
    <Steps
      current={currentIndex}
      size="small"
      style={{ maxWidth: 700, margin: '0 auto' }}
      onChange={(i) => {
        if (i <= reachedIndex && onStageClick) onStageClick(STAGES[i].key)
      }}
      items={STAGES.map((s, i) => ({
        title: s.title,
        status: i < reachedIndex ? 'finish' : i === currentIndex ? 'process' : 'wait',
        disabled: i > reachedIndex,   // 没走到的那几步点不动，且鼠标是禁用样式
      }))}
    />
  )
}
