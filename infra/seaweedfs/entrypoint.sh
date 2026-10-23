#!/bin/sh
# 근거 문서 저장소 (SPEC-document-store 3.1). SeaweedFS 한 프로세스로 master·volume·filer·S3를 띄운다.
# S3 계정은 backend 하나뿐이고 문서 버킷만 읽기·쓰기한다. 계정이 정의돼 있으면 익명 요청은 거부된다.
# 비밀 값은 출력하지 않는다.
set -eu

umask 077
cat > /tmp/s3.json <<JSON
{
  "identities": [
    {
      "name": "opslens-backend",
      "credentials": [{"accessKey": "$DOCUMENTS_S3_ACCESS_KEY", "secretKey": "$DOCUMENTS_S3_SECRET_KEY"}],
      "actions": ["Read:$DOCUMENTS_S3_BUCKET", "Write:$DOCUMENTS_S3_BUCKET", "List:$DOCUMENTS_S3_BUCKET"]
    }
  ]
}
JSON

exec weed server -dir=/data -ip=seaweedfs -ip.bind=0.0.0.0 -volume.max=0 \
  -s3 -s3.port=8333 -s3.config=/tmp/s3.json -s3.iam=false
