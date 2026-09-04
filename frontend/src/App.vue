<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import ConversationList from './components/ConversationList.vue'
import { api, getActiveConversationId, setActiveConversationId } from './api'

marked.setOptions({ breaks: true, gfm: true })

const messages = ref([])
const conversations = ref([])
const activeConversationId = ref('')
const question = ref('')
const files = ref([])
const status = ref({ ollama_connected: false, chunk_count: 0 })
const busy = ref(false)
const uploadBusy = ref(false)
const historyLoading = ref(false)
const notice = ref('')
const streamStatus = ref('')
const messageList = ref(null)
let historyRequestSequence = 0
let scrollScheduled = false
let localMessageSequence = 0

const orchestrated = ref(false)

// Wiki compilation runs in the background after the upload call returns, so it
// is deliberately absent from `interactionLocked`: waiting minutes for a model
// before the user may ask anything would undo the point of doing it in the
// background at all.
const wikiJob = ref(null)
const WIKI_POLL_INTERVAL_MS = 2000
// `idle` means the backend no longer knows this job — job state is in-memory,
// so a restart loses it. Terminal, or the timer would poll a job that can never
// report anything again.
const WIKI_TERMINAL_STATUSES = ['published', 'ignored', 'failed', 'cancelled', 'idle']
let wikiPollTimer = null

const interactionLocked = computed(() => busy.value || uploadBusy.value || historyLoading.value)
const activeTitle = computed(() => (
  conversations.value.find((item) => item.id === activeConversationId.value)?.title || '企业制度问答'
))
// Wiki and inventory questions do not need an uploaded document, so the
// composer must not stay locked behind an empty knowledge base in this mode.
const canAsk = computed(() => orchestrated.value || status.value.chunk_count > 0)

const WIKI_STATUS_LABELS = {
  queued: 'Wiki 编译排队中',
  running: '正在编译 Wiki',
  published: 'Wiki 已更新',
  ignored: '文档无需编入 Wiki',
  failed: 'Wiki 编译失败，继续使用原有 Wiki',
  cancelled: 'Wiki 编译已取消',
  idle: 'Wiki 任务状态已丢失，可重新上传',
}

const WIKI_STAGE_LABELS = {
  document_decision: '判断文档是否收录',
  topic_plan: '规划 Wiki 页面',
}

function wikiStageLabel(stage) {
  if (!stage) return ''
  if (WIKI_STAGE_LABELS[stage]) return WIKI_STAGE_LABELS[stage]
  if (stage.startsWith('page_compilation:')) return `编写页面「${stage.slice('page_compilation:'.length)}」`
  return stage
}

const wikiMessage = computed(() => {
  const job = wikiJob.value
  if (!job) return ''
  const label = WIKI_STATUS_LABELS[job.status] || job.status
  const parts = [label]
  if (job.total_documents > 1) parts.push(`${job.completed_documents}/${job.total_documents} 个文件`)
  const stage = wikiStageLabel(job.stage)
  if (stage) parts.push(stage)
  if (job.status === 'failed' && job.error) parts.push(job.error)
  return parts.join(' · ')
})

const wikiActive = computed(() => ['queued', 'running'].includes(wikiJob.value?.status))

const EVIDENCE_LABELS = { wiki: 'Wiki', document: '原文', system: '实时状态' }
const STEP_LABELS = { wiki_query: 'Wiki', document_search: '原文检索', system_query: '实时查询' }
const ROUTE_LABELS = {
  direct: '直接回答',
  wiki_only: 'Wiki',
  document_only: '原文',
  system_only: '实时状态',
  wiki_document: 'Wiki + 原文',
  wiki_system: 'Wiki + 实时状态',
  document_system: '原文 + 实时状态',
  wiki_document_system: 'Wiki + 原文 + 实时状态',
}

const evidenceLabel = (type) => EVIDENCE_LABELS[type] || type
const stepLabel = (step) => STEP_LABELS[step] || step
const routeLabel = (route) => ROUTE_LABELS[route] || route
const hasValue = (value) => value !== null && value !== undefined

function renderMarkdown(content = '') {
  return DOMPurify.sanitize(marked.parse(content), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['img', 'style'],
    FORBID_ATTR: ['style'],
  })
}

function normalizeMessages(items = []) {
  return items.map((item) => ({
    id: item.id,
    role: item.role,
    content: item.content || '',
    sources: item.sources || [],
    trace: item.trace || null,
    createdAt: item.created_at,
  }))
}

function nextLocalMessageId(role) {
  localMessageSequence += 1
  return `local-${role}-${localMessageSequence}`
}

function scheduleScroll() {
  if (scrollScheduled) return
  scrollScheduled = true
  requestAnimationFrame(async () => {
    await nextTick()
    if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
    scrollScheduled = false
  })
}

function formatDuration(value) {
  const seconds = Number(value)
  return Number.isFinite(seconds) ? `${seconds.toFixed(2)}s` : ''
}

async function refreshStatus() {
  try {
    status.value = await api.health()
  } catch (error) {
    notice.value = error.message
  }
}

async function refreshConversationList() {
  try {
    const result = await api.listConversations()
    conversations.value = result.items || []
  } catch (error) {
    notice.value = error.message
  }
}

async function initializeConversations() {
  const sequence = ++historyRequestSequence
  historyLoading.value = true
  try {
    const result = await api.listConversations()
    if (sequence !== historyRequestSequence) return
    conversations.value = result.items || []

    const savedId = getActiveConversationId()
    const target = conversations.value.find((item) => item.id === savedId) || conversations.value[0]
    if (!target) {
      activeConversationId.value = ''
      setActiveConversationId('')
      messages.value = []
      return
    }

    activeConversationId.value = target.id
    setActiveConversationId(target.id)
    const history = await api.conversationMessages(target.id)
    if (sequence !== historyRequestSequence || activeConversationId.value !== target.id) return
    messages.value = normalizeMessages(history.items)
    scheduleScroll()
  } catch (error) {
    if (sequence === historyRequestSequence) notice.value = error.message
  } finally {
    if (sequence === historyRequestSequence) historyLoading.value = false
  }
}

async function selectConversation(conversationId) {
  if (!conversationId || interactionLocked.value) return

  const sequence = ++historyRequestSequence
  historyLoading.value = true
  notice.value = ''
  activeConversationId.value = conversationId
  setActiveConversationId(conversationId)
  messages.value = []

  try {
    const result = await api.conversationMessages(conversationId)
    if (sequence !== historyRequestSequence || activeConversationId.value !== conversationId) return
    messages.value = normalizeMessages(result.items)
    scheduleScroll()
  } catch (error) {
    if (sequence !== historyRequestSequence) return
    notice.value = error.message
    activeConversationId.value = ''
    setActiveConversationId('')
    await refreshConversationList()
  } finally {
    if (sequence === historyRequestSequence) historyLoading.value = false
  }
}

function activateConversation(item) {
  conversations.value = [item, ...conversations.value.filter((entry) => entry.id !== item.id)]
  activeConversationId.value = item.id
  setActiveConversationId(item.id)
  messages.value = []
}

async function createConversation() {
  if (interactionLocked.value) return
  const sequence = ++historyRequestSequence
  historyLoading.value = true
  notice.value = ''
  try {
    const item = await api.createConversation()
    if (sequence !== historyRequestSequence) return
    activateConversation(item)
    notice.value = '已开始新对话，知识库保持不变'
  } catch (error) {
    if (sequence === historyRequestSequence) notice.value = error.message
  } finally {
    if (sequence === historyRequestSequence) historyLoading.value = false
  }
}

async function ensureActiveConversation(title) {
  if (activeConversationId.value) return activeConversationId.value
  const item = await api.createConversation(title.slice(0, 60) || '新对话')
  activateConversation(item)
  return item.id
}

async function removeConversation(conversationId) {
  if (!conversationId || interactionLocked.value) return
  const item = conversations.value.find((entry) => entry.id === conversationId)
  if (!globalThis.confirm(`确定删除“${item?.title || '这条对话'}”吗？`)) return

  const sequence = ++historyRequestSequence
  historyLoading.value = true
  notice.value = ''
  try {
    await api.deleteConversation(conversationId)
    if (sequence !== historyRequestSequence) return
    conversations.value = conversations.value.filter((entry) => entry.id !== conversationId)

    if (activeConversationId.value !== conversationId) return
    messages.value = []
    const nextConversation = conversations.value[0]
    if (!nextConversation) {
      activeConversationId.value = ''
      setActiveConversationId('')
      return
    }

    activeConversationId.value = nextConversation.id
    setActiveConversationId(nextConversation.id)
    const result = await api.conversationMessages(nextConversation.id)
    if (sequence !== historyRequestSequence || activeConversationId.value !== nextConversation.id) return
    messages.value = normalizeMessages(result.items)
    scheduleScroll()
  } catch (error) {
    if (sequence === historyRequestSequence) notice.value = error.message
  } finally {
    if (sequence === historyRequestSequence) historyLoading.value = false
  }
}

function resetConversations() {
  historyRequestSequence += 1
  historyLoading.value = false
  conversations.value = []
  activeConversationId.value = ''
  setActiveConversationId('')
  messages.value = []
}

function choose(event) {
  files.value = [...event.target.files]
  event.target.value = ''
}

async function upload() {
  if (!files.value.length || interactionLocked.value) return
  uploadBusy.value = true
  notice.value = ''
  try {
    const result = await api.upload(files.value)
    status.value.chunk_count = result.chunks
    files.value = []
    resetConversations()
    notice.value = `已导入 ${result.files.length} 个文件，生成 ${result.chunks} 个知识片段`
    // Retrieval is already usable; the Wiki catches up in the background.
    if (result.wiki_job_id) startWikiPolling(result.wiki_job_id)
  } catch (error) {
    notice.value = error.message
  } finally {
    uploadBusy.value = false
  }
}

function stopWikiPolling() {
  if (wikiPollTimer) {
    clearTimeout(wikiPollTimer)
    wikiPollTimer = null
  }
}

function startWikiPolling(jobId) {
  stopWikiPolling()
  wikiJob.value = { job_id: jobId, status: 'queued', stage: null, files: [], completed_documents: 0, total_documents: 0 }
  const poll = async () => {
    try {
      const job = await api.wikiStatus(jobId)
      wikiJob.value = job
      if (WIKI_TERMINAL_STATUSES.includes(job.status)) {
        stopWikiPolling()
        return
      }
    } catch {
      // A dropped poll is not a failed compile; keep watching.
    }
    wikiPollTimer = setTimeout(poll, WIKI_POLL_INTERVAL_MS)
  }
  wikiPollTimer = setTimeout(poll, WIKI_POLL_INTERVAL_MS)
}

async function send() {
  const content = question.value.trim()
  if (!content || interactionLocked.value) return

  busy.value = true
  notice.value = ''
  let answerIndex = -1
  try {
    const conversationId = await ensureActiveConversation(content)
    messages.value.push({ id: nextLocalMessageId('user'), role: 'user', content })
    answerIndex = messages.value.push({ id: nextLocalMessageId('assistant'), role: 'assistant', content: '', sources: [], trace: null }) - 1
    question.value = ''
    streamStatus.value = '正在连接 Agent'
    scheduleScroll()

    await api.chatStream(content, conversationId, (event, data) => {
      const answer = messages.value[answerIndex]
      if (!answer) return
      if (event === 'status') streamStatus.value = data.message
      if (event === 'delta') answer.content += data.content
      if (event === 'sources') answer.sources = data.sources
      if (event === 'done') {
        answer.trace = data.trace
        streamStatus.value = ''
      }
      scheduleScroll()
    }, undefined, orchestrated.value ? 'orchestrated' : 'legacy')
    await refreshConversationList()
  } catch (error) {
    if (answerIndex < 0) {
      notice.value = error.message
    } else {
      const answer = messages.value[answerIndex]
      answer.error = true
      answer.errorMessage = answer.content ? `响应中断：${error.message}` : `请求失败：${error.message}`
    }
  } finally {
    busy.value = false
    streamStatus.value = ''
    scheduleScroll()
  }
}

async function clearKnowledge() {
  if (interactionLocked.value) return
  uploadBusy.value = true
  try {
    await api.clear()
    resetConversations()
    files.value = []
    status.value.chunk_count = 0
    stopWikiPolling()
    wikiJob.value = null
    notice.value = '知识库已清空'
  } catch (error) {
    notice.value = error.message
  } finally {
    uploadBusy.value = false
  }
}

function handleComposerKeydown(event) {
  if (event.isComposing || event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  send()
}

onMounted(() => Promise.allSettled([refreshStatus(), initializeConversations()]))
</script>

<template>
  <main class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">K</span>
        <div><strong>Knowledge Agent</strong><small>企业知识助手</small></div>
      </div>

      <ConversationList
        :items="conversations"
        :active-id="activeConversationId"
        :disabled="interactionLocked"
        :loading="historyLoading"
        @create="createConversation"
        @select="selectConversation"
        @remove="removeConversation"
      />

      <section class="panel status-panel">
        <div class="section-title">
          <span>系统状态</span>
          <span class="status-dot" :class="{ online: status.ollama_connected }"></span>
        </div>
        <p class="muted">{{ status.ollama_connected ? 'Ollama 已连接' : 'Ollama 未连接' }}</p>
        <p class="metric"><strong>{{ status.chunk_count }}</strong><span>知识片段</span></p>
      </section>

      <section class="panel upload-panel">
        <div class="section-title">导入知识</div>
        <label class="dropzone">
          <input type="file" multiple accept=".pdf,.txt,.md" :disabled="interactionLocked" @change="choose">
          <span class="upload-icon">↑</span>
          <strong>选择 PDF / TXT / MD</strong>
          <small>{{ files.length ? `已选择 ${files.length} 个文件` : '支持多文件上传' }}</small>
        </label>
        <button class="primary" :disabled="!files.length || interactionLocked || !status.ollama_connected" @click="upload">
          {{ uploadBusy ? '正在建立索引…' : '建立知识库' }}
        </button>
        <!-- Background compilation: the composer stays usable throughout. -->
        <p
          v-if="wikiMessage"
          class="wiki-job"
          :class="{ active: wikiActive, failed: wikiJob?.status === 'failed' }"
          role="status"
          aria-live="polite"
        >
          <i v-if="wikiActive"></i>{{ wikiMessage }}
        </p>
      </section>
      <button class="ghost danger" :disabled="!status.chunk_count || interactionLocked" @click="clearKnowledge">清空知识库</button>
    </aside>

    <section class="chat">
      <header>
        <div><h1>{{ activeTitle }}</h1><p>混合检索 · Agent 工具调用 · 可追溯引用</p></div>
        <div class="header-actions">
          <label class="mode-toggle" :class="{ on: orchestrated }">
            <input v-model="orchestrated" type="checkbox" :disabled="interactionLocked">
            <span>三通道模式</span>
          </label>
          <button :disabled="interactionLocked" @click="createConversation">新对话</button>
          <span class="badge">Local RAG</span>
        </div>
      </header>
      <div v-if="notice" class="notice">{{ notice }}</div>

      <div ref="messageList" class="messages" :aria-busy="historyLoading">
        <div v-if="historyLoading" class="history-loading"><i></i><span>正在恢复会话…</span></div>
        <div v-else-if="!messages.length" class="empty">
          <div class="orb">✦</div>
          <h2>从企业知识中获得可靠答案</h2>
          <p>上传资料后，可以查询制度、比较规则、列出来源或总结知识库。</p>
          <div class="suggestions">
            <button v-for="item in ['年假如何申请？', '比较年假和调休制度', '列出知识库资料来源']" :key="item" @click="question = item">{{ item }}</button>
          </div>
        </div>

        <article v-for="message in messages" :key="message.id" class="message" :class="message.role">
          <div class="avatar">{{ message.role === 'user' ? '你' : 'AI' }}</div>
          <div class="message-body" :class="{ error: message.error }">
            <div v-if="message.role === 'assistant'" class="markdown" v-html="renderMarkdown(message.content)"></div>
            <p v-else>{{ message.content }}</p>
            <span v-if="message.role === 'assistant' && !message.content && busy" class="cursor"></span>
            <p v-if="message.errorMessage" class="stream-error">{{ message.errorMessage }}</p>
            <div v-if="message.trace?.route" class="evidence-path">
              <span class="route">{{ routeLabel(message.trace.route) }}</span>
              <span v-for="step in message.trace.steps" :key="step" class="step">{{ stepLabel(step) }}</span>
            </div>
            <details v-if="message.sources?.length">
              <summary>查看 {{ message.sources.length }} 条检索依据</summary>
              <div v-for="source in message.sources" :key="source.rank" class="source">
                <strong>
                  <span v-if="source.type" class="evidence-tag" :class="source.type">{{ evidenceLabel(source.type) }}</span>
                  {{ source.rank }}. {{ source.heading || source.locator || source.source }}
                </strong>
                <small>
                  {{ source.source }}
                  <template v-if="hasValue(source.chunk_index)"> · 片段 {{ source.chunk_index }}</template>
                  <template v-if="hasValue(source.score)"> · {{ source.score }}</template>
                </small>
                <p>{{ source.content }}</p>
              </div>
            </details>
            <small v-if="message.trace" class="trace">{{ message.trace.tool || routeLabel(message.trace.route) }} · {{ formatDuration(message.trace.total_seconds) }}</small>
          </div>
        </article>
      </div>

      <footer>
        <div v-if="streamStatus" class="stream-status" role="status" aria-live="polite"><i></i>{{ streamStatus }}</div>
        <div class="composer">
          <textarea v-model="question" rows="1" :disabled="!canAsk || interactionLocked" :placeholder="orchestrated ? '输入问题，可查询制度或库存…' : '输入企业制度问题…'" @keydown="handleComposerKeydown"></textarea>
          <button :disabled="!question.trim() || interactionLocked || !canAsk" @click="send">{{ busy ? '回答中' : '发送' }}</button>
        </div>
        <small>回答仅基于已导入资料，请核对引用来源。</small>
      </footer>
    </section>
  </main>
</template>
