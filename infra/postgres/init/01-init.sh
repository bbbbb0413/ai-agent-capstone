#!/bin/bash
# 서비스별 계정과 스키마를 만든다 (기획안 3장 "PostgreSQL 스키마와 계정 분리").
#   app   : backend만 읽고 쓴다 (업무 데이터, 작업 이력, 감사 로그)
#   agent : agent만 읽고 쓴다 (LangGraph checkpointer, store)
#   docs  : indexer가 쓰고 rag-mcp는 읽기만 한다 (문서 chunk, Embedding)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE ROLE opslens_app     LOGIN PASSWORD '${APP_DB_PASSWORD}';
CREATE ROLE opslens_agent   LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
CREATE ROLE opslens_indexer LOGIN PASSWORD '${INDEXER_DB_PASSWORD}';
CREATE ROLE opslens_rag     LOGIN PASSWORD '${RAG_DB_PASSWORD}';

CREATE SCHEMA app   AUTHORIZATION opslens_app;
CREATE SCHEMA agent AUTHORIZATION opslens_agent;
CREATE SCHEMA docs  AUTHORIZATION opslens_indexer;

GRANT USAGE ON SCHEMA docs TO opslens_rag;
ALTER DEFAULT PRIVILEGES FOR ROLE opslens_indexer IN SCHEMA docs GRANT SELECT ON TABLES TO opslens_rag;

ALTER ROLE opslens_app     SET search_path = app;
ALTER ROLE opslens_agent   SET search_path = agent;
ALTER ROLE opslens_indexer SET search_path = docs, public;
ALTER ROLE opslens_rag     SET search_path = docs, public;
SQL
