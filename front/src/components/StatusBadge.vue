<script setup>
import { computed } from 'vue'

const props = defineProps({
  kind: { type: String, required: true }, // task | incident | severity
  value: { type: String, default: '' },
})

const STYLES = {
  task: {
    DONE: ['성공', 'ok'],
    FAILED: ['실패', 'bad'],
    WAITING_APPROVAL: ['승인 대기', 'warn'],
    CHANGE_PENDING: ['반영 대기', 'warn'],
    RUNNING: ['진행 중', 'info'],
    QUEUED: ['대기열', 'info'],
  },
  incident: { OPEN: ['진행 중', 'bad'], MITIGATED: ['완화됨', 'warn'], RESOLVED: ['해결됨', 'ok'] },
  severity: { P1: ['P1', 'bad'], P2: ['P2', 'warn'], P3: ['P3', 'neutral'] },
}

const style = computed(() => STYLES[props.kind]?.[props.value] || [props.value, 'neutral'])
</script>

<template>
  <span class="badge" :class="style[1]">{{ style[0] }}</span>
</template>
