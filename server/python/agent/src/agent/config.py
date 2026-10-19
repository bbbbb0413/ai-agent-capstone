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
    dispatch_mode: str = _env("DISPATCH_MODE", "http")
    kafka_bootstrap_servers: str = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    prompts_dir: str = _env("PROMPTS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "prompts"))
    workers: int = field(default_factory=lambda: int(os.environ.get("AGENT_WORKERS", "4")))


settings = Settings()
