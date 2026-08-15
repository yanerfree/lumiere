/**
 * SSE 增量解析器 —— 事件名和缓冲区都必须活过网络分片。
 *
 * 修的是这个事故：UI 执行面板永远停在「正在收尾」，转圈不结束，
 * 而后端那次其实 13.2 秒就 passed 了、库里记录完整。
 *
 * 原因是解析写成了「每读一个分片，重来一遍」：
 *
 *     function processChunk() {
 *       reader.read().then(({ value }) => {
 *         buffer += decode(value)
 *         const lines = buffer.split('\n')
 *         buffer = lines.pop()        // 半行留着 —— 这里是对的
 *         let currentEvent = null     // ← 事件名却每片清零
 *         for (const line of lines) { ... }
 *         processChunk()
 *       })
 *     }
 *
 * `done` 那一帧有 47KB（37 个步骤 13KB + 96 条流量 34KB），一个分片装不下。
 * 于是它被劈成：
 *
 *     分片 A: "event: done\ndata: {\"status\":\"passed\",..."   ← 后半截没换行
 *     分片 B: "...余下 40KB...}\n\n"
 *
 * A 里 `event: done` 是完整行 → currentEvent='done'；`data:` 那半行被 pop 进
 * buffer 等下一片。**然后函数返回，currentEvent 跟着没了。**
 * B 里 data 行拼完整了，却因为 `&& currentEvent` 为 null 被整条跳过。
 *
 * 结果：done 事件被静默丢弃 —— 不报错、不重试、没有任何痕迹，前端就一直转圈。
 * 帧越大越必然触发，跟运气无关。
 *
 * 所以事件名和 buffer 一样，得放在跨分片存活的位置。
 */
export function createSseParser(onEvent) {
  let buffer = ''
  let currentEvent = null

  return {
    push(text) {
      buffer += text
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const ev = currentEvent
          currentEvent = null
          if (!ev) continue
          let data
          try {
            data = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          onEvent(ev, data)
        }
      }
    },
  }
}
