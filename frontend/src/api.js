const CLIENT_KEY = 'knowledge-agent-client-id'
const ACTIVE_CONVERSATION_KEY = 'knowledge-agent-active-conversation-id'
let memoryClientId = ''

function createId(prefix) {
  const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${value}`
}

function readLocalStorage(key) {
  try {
    return globalThis.localStorage?.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeLocalStorage(key, value) {
  try {
    if (value) globalThis.localStorage?.setItem(key, value)
    else globalThis.localStorage?.removeItem(key)
  } catch {
    // The app still works for this page when private-mode storage is unavailable.
  }
}

export function getClientId() {
  let clientId = readLocalStorage(CLIENT_KEY) || memoryClientId
  if (!clientId) {
    clientId = createId('client')
    writeLocalStorage(CLIENT_KEY, clientId)
  }
  memoryClientId = clientId
  return clientId
}

export function getActiveConversationId() {
  return readLocalStorage(ACTIVE_CONVERSATION_KEY)
}

export function setActiveConversationId(conversationId) {
  writeLocalStorage(ACTIVE_CONVERSATION_KEY, conversationId)
}

async function request(path, options = {}) {
  const response = await fetch(path, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || '请求失败，请检查后端服务')
  return data
}

function withClient(path) {
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}client_id=${encodeURIComponent(getClientId())}`
}

function chatPayload(question, conversationId, mode) {
  return {
    question,
    session_id: conversationId,
    client_id: getClientId(),
    // Omitted by older callers, so the backend keeps its legacy behavior.
    mode: mode === 'orchestrated' ? 'orchestrated' : 'legacy',
  }
}

export const api = {
  health: () => request('/api/health'),

  upload(files) {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    return request('/api/knowledge/upload', { method: 'POST', body })
  },

  clear: () => request('/api/knowledge', { method: 'DELETE' }),

  listConversations: () => request(withClient('/api/conversations')),

  createConversation(title = '新对话') {
    return request('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, client_id: getClientId() }),
    })
  },

  conversationMessages(conversationId) {
    return request(withClient(`/api/conversations/${encodeURIComponent(conversationId)}/messages`))
  },

  deleteConversation(conversationId) {
    return request(withClient(`/api/conversations/${encodeURIComponent(conversationId)}`), { method: 'DELETE' })
  },

  chat(question, conversationId, mode) {
    return request('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(chatPayload(question, conversationId, mode)),
    })
  },

  async chatStream(question, conversationId, onEvent, signal, mode) {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(chatPayload(question, conversationId, mode)),
      signal,
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data.detail || '无法开始流式回答')
    }
    if (!response.body) throw new Error('当前浏览器不支持流式响应')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let completed = false
    try {
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        buffer = buffer.replace(/\r\n/g, '\n')
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() || ''
        for (const block of blocks) {
          const lines = block.split('\n')
          const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message'
          const raw = lines
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trimStart())
            .join('\n')
          if (!raw) continue
          const data = JSON.parse(raw)
          if (event === 'error') throw new Error(data.message || '流式回答失败')
          if (event === 'done') completed = true
          onEvent(event, data)
        }
        if (done) break
      }
      if (!completed) throw new Error('流式连接提前结束，请重试')
    } finally {
      if (!completed) await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  },
}
