"""첨부 자료를 읽고 현재 지표와 비교한다. 원문/이미지는 Graph checkpoint에 보관하지 않는다."""
import base64
import json
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import tracing_context

from agent import harness, signals
from agent.backend import backend

TEXT_CHARS = 16_000
_SECRET_PATTERNS = [
    (re.compile(r'(?i)(["\']?(?:password|passwd|api[_-]?key|access[_-]?token|secret)["\']?\s*[:=]\s*)(?:"[^"\n]*"|\'[^\'\n]*\'|[^\s,;]+)'), r'\1[REDACTED]'),
    (re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]+|gh[pousr]_[A-Za-z0-9]+)\b'), '[REDACTED]'),
]


def mask(text: str) -> str:
    text = harness.mask(text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def excerpt(text: str) -> tuple[str, bool]:
    """긴 로그는 앞뒤를 표시한다. 누락 범위와 원래 줄 번호를 유지한다."""
    text = mask(text)
    numbered = [f"L{i}: {line}" for i, line in enumerate(text.splitlines(), 1)]
    full = '\n'.join(numbered)
    if len(full) <= TEXT_CHARS:
        return full, False
    return full[:TEXT_CHARS//2] + '\n[중간 내용 생략 — 전체 로그를 분석한 결과가 아님]\n' + full[-TEXT_CHARS//2:], True


def content_parts(files: list[dict]) -> tuple[list[dict], list[dict]]:
    if not 1 <= len(files) <= 3:
        raise ValueError('첨부 파일이 없거나 개수 한도를 초과했다')
    parts, evidence = [], []
    for file in files:
        name, kind = file['filename'], file['mediaType']
        raw = base64.b64decode(file['content'], validate=True)
        if not raw or len(raw) > 4*1024*1024:
            raise ValueError('잘못된 첨부 파일 크기')
        parts.append({'type': 'text', 'text': f"첨부 자료 이름: {json.dumps(name, ensure_ascii=False)}"})
        shortened = False
        if kind == 'text/plain':
            text, shortened = excerpt(raw.decode('utf-8-sig'))
            parts.append({'type': 'text', 'text': harness.as_untrusted(text)})
        elif kind in ('image/png', 'image/jpeg'):
            parts.append({'type': 'image_url', 'image_url': {'url': f'data:{kind};base64,' + base64.b64encode(raw).decode(), 'detail': 'high'}})
        else:
            raise ValueError('지원하지 않는 첨부 형식')
        evidence.append({'tool': 'attachment', 'filename': name, 'id': file['id'], 'status': 'success',
                         'summary': '로그 앞뒤 발췌 — 중간 생략' if shortened else '사용자 첨부 자료 (현재 운영 상태와 별도)',
                         'truncated': shortened})
    return parts, evidence


async def analyze(state, runtime) -> dict:
    runtime.context.check_time()
    await backend.emit(state['task_id'], 'STEP', {'node': 'analyze_attachments'})
    files = await backend.attachments(state['task_id'], state['tenant_id'])
    parts, evidence = content_parts(files)
    counts = dict(state.get('tool_counts', {}))
    # 서비스마다 서비스 카탈로그의 관측 정의로 조회한다. 정의가 없는 서비스는 조회하지 않는다 (SPEC-limitations-fixes 4단계)
    labels = {'error_rate': '에러율', 'p95_seconds': '응답 p95(초)', 'requests_per_second': '요청량(초당)'}
    context = state.get('context', {})
    queries = {f'{name} {labels[key]}': expr
               for name in [s.get('name') for s in context.get('services') or [] if isinstance(s, dict)]
               if (obs := signals.observability(context, name))
               for key, expr in signals.metric_queries(obs, name, '1m').items()}
    metrics = {} if queries else {'지표': {'status': 'error', 'data': '관측 정의가 있는 서비스가 없어 확인 불가'}}
    for label, expr in queries.items():
        try:
            result = await runtime.context.toolbox.call('analyze_attachments', 'grafana_query_prometheus',
                                                        {'expr': expr, 'queryType': 'instant', 'endTime': 'now'}, counts)
            metrics[label] = {'status': result.status, 'data': str(result.content)}
        except Exception:
            metrics[label] = {'status': 'error', 'data': '모니터링 서버에 연결할 수 없어 확인 불가'}
    evidence.append({'tool': 'grafana_query_prometheus', 'summary': json.dumps(metrics, ensure_ascii=False),
                     'observedAt': datetime.now(timezone.utc).isoformat()})
    parts.insert(0, {'type': 'text', 'text': f"질문: {mask(state['question'])}\n고객사 컨텍스트:\n"
                  + json.dumps(state.get('context', {}), ensure_ascii=False, default=str)
                  + '\n현재 지표(최근 1분, 첨부 시각과 다를 수 있음):\n' + json.dumps(metrics, ensure_ascii=False)})
    # 이미지 원문을 외부 tracing 서비스와 Graph state에 복제하지 않는다.
    with tracing_context(enabled=False):
        response = await harness.chat_model().ainvoke([
            SystemMessage(harness.prompt('attachment')), HumanMessage(content=parts)])
    text = response.content if isinstance(response.content, str) else '\n'.join(
        p.get('text', '') for p in response.content if isinstance(p, dict))
    if not text.strip():
        raise ValueError('첨부 분석 결과가 비어 있다')
    return {'findings': mask(text), 'evidence': state.get('evidence', []) + evidence,
            'tool_counts': counts, 'turns': state.get('turns', 0) + 1}
