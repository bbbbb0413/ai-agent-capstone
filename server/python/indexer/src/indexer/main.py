"""indexer 공통 설정. 문서 색인은 indexer.documents, 회고 색인은 indexer.postmortems가 한다.

문서는 근거 문서 저장소(SPEC-document-store)에서 오고, 형식별 파서(parse.py)와 공통 청커(chunk.py)로
조각을 만든 뒤 질문 검색과 같은 Embedding 모델로 벡터화한다. 같은 문서를 다시 색인하면 이전 조각을 지우고 새로 넣는다.
"""

import os
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://opslens_indexer@localhost:5432/opslens")
# 회고 색인이 고객사 목록을 정할 때만 쓴다(POSTMORTEM_TENANTS가 없을 때). 문서 색인은 디렉터리를 읽지 않는다
DOCUMENTS_DIR = Path(os.environ.get("DOCUMENTS_DIR", "data/documents"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))
