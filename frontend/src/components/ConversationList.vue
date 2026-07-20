<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

defineEmits(['create', 'select', 'remove'])

const formatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : formatter.format(date)
}
</script>

<template>
  <section class="conversation-panel" aria-label="会话历史">
    <div class="conversation-heading">
      <span>会话历史</span>
      <button
        class="conversation-create"
        type="button"
        :disabled="disabled"
        aria-label="新建对话"
        title="新建对话"
        @click="$emit('create')"
      >
        ＋
      </button>
    </div>

    <div class="conversation-scroll" :aria-busy="loading">
      <p v-if="loading && !items.length" class="conversation-empty">正在恢复会话…</p>
      <p v-else-if="!items.length" class="conversation-empty">还没有历史对话</p>

      <div
        v-for="item in items"
        :key="item.id"
        class="conversation-row"
        :class="{ active: item.id === activeId }"
      >
        <button
          class="conversation-main"
          type="button"
          :disabled="disabled"
          :aria-current="item.id === activeId ? 'page' : undefined"
          @click="$emit('select', item.id)"
        >
          <span class="conversation-title" :title="item.title || '新对话'">{{ item.title || '新对话' }}</span>
          <small>{{ formatTime(item.updated_at) }}</small>
        </button>
        <button
          class="conversation-remove"
          type="button"
          :disabled="disabled"
          :aria-label="`删除会话：${item.title || '新对话'}`"
          title="删除会话"
          @click="$emit('remove', item.id)"
        >
          ×
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.conversation-panel {
  display: flex;
  min-height: 150px;
  max-height: 270px;
  flex: 1 1 210px;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #ffffff12;
  border-radius: 16px;
  background: #ffffff05;
}

.conversation-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px 16px;
  color: #b9c8c3;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .05em;
}

.conversation-create {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid #7ee2b844;
  border-radius: 8px;
  color: #7ee2b8;
  background: #7ee2b80a;
  font-size: 18px;
  line-height: 1;
}

.conversation-create:hover:not(:disabled) {
  border-color: #7ee2b888;
  background: #7ee2b816;
}

.conversation-scroll {
  min-height: 0;
  padding: 2px 7px 9px;
  overflow-y: auto;
}

.conversation-empty {
  margin: 22px 8px;
  color: #71847d;
  text-align: center;
  font-size: 12px;
}

.conversation-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 30px;
  align-items: center;
  border-radius: 10px;
  transition: background .16s ease;
}

.conversation-row:hover,
.conversation-row.active {
  background: #7ee2b80d;
}

.conversation-row.active {
  box-shadow: inset 2px 0 #7ee2b8;
}

.conversation-main,
.conversation-remove {
  border: 0;
  background: transparent;
}

.conversation-main {
  display: block;
  min-width: 0;
  padding: 9px 7px 9px 11px;
  color: #b9c8c3;
  text-align: left;
}

.conversation-title,
.conversation-main small {
  display: block;
}

.conversation-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.conversation-main small {
  margin-top: 3px;
  color: #60736d;
  font-size: 10px;
}

.conversation-row.active .conversation-title {
  color: #7ee2b8;
}

.conversation-remove {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  color: #60736d;
  font-size: 17px;
  opacity: 0;
}

.conversation-row:hover .conversation-remove,
.conversation-row:focus-within .conversation-remove {
  opacity: 1;
}

.conversation-remove:hover:not(:disabled) {
  color: #ff8f86;
  background: #ff8f8610;
}

@media (max-width: 760px) {
  .conversation-panel {
    max-height: 220px;
    flex-basis: 160px;
  }
}
</style>
