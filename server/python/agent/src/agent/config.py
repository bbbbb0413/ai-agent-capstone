"""환경변수 설정. 비밀 값은 compose가 .env에서 넘긴다."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return field(default_factory=lambda: os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    backend_url: str = _env("BACKEND_URL", "http://localhost:8080")
    service_token: str = _env("SERVICE_TOKEN")
    database_url: str = _env("DATABASE_URL", "postgresql://opslens_agent@localhost:5432/opslens")
    business_mcp_url: str = _env("BUSINESS_MCP_URL", "http://localhost:9101/mcp")
    rag_mcp_url: str = _env("RAG_MCP_URL", "http://localhost:9102/mcp")
    grafana_mcp_url: str = _env("GRAFANA_MCP_URL", "http://localhost:9103/mcp")
    llm_model: str = _env("LLM_MODEL", "gpt-5-mini")
    fallback_model: str = _env("FALLBACK_MODEL", "gpt-5-nano")
    # 추론 모델(gpt-5 계열)용. 비우면 모델 기본값을 쓴다. 추론을 지원하지 않는 모델이면 비워야 한다
    reasoning_effort: str = _env("LLM_REASONING_EFFORT", "low")
    # 진단 첫 턴에 도구 호출을 강제한다. 강제 없이 비교할 때만 false로 둔다
    diagnose_force_first_tool: bool = field(
        default_factory=lambda: os.environ.get("DIAGNOSE_FORCE_FIRST_TOOL", "true").lower() != "false")
    dispatch_mode: str = _env("DISPATCH_MODE", "kafka")
    kafka_bootstrap_servers: str = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    prompts_dir: str = _env("PROMPTS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "prompts"))
    alembic_dir: str = _env("ALEMBIC_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "migrations"))
    workers: int = field(default_factory=lambda: int(os.environ.get("AGENT_WORKERS", "4")))
    # Kafka 소비 역할. all이면 한 프로세스가 Gold·Silver 루프를 모두 돌리고, gold·silver면 그 등급만 (SPEC-kafka-scale 4.3(2))
    consumer_role: str = _env("AGENT_CONSUMER_ROLE", "all")


settings = Settings()
