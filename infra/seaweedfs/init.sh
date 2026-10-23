#!/bin/sh
# 문서 버킷을 만든다 (SPEC-document-store 3.1). 이미 있으면 그대로 둔다. 다시 돌려도 결과가 같다.
# weed shell은 master·filer에 바로 붙으므로 S3 관리자 계정이 필요 없다.
set -eu

shell() { echo "$1" | weed shell -master=seaweedfs:9333 -filer=seaweedfs:8888 2>&1; }

if shell "s3.bucket.list" | grep -q "^  $DOCUMENTS_S3_BUCKET"; then
  echo "seaweedfs-init: bucket $DOCUMENTS_S3_BUCKET exists"
else
  shell "s3.bucket.create -name $DOCUMENTS_S3_BUCKET" > /dev/null
  echo "seaweedfs-init: bucket $DOCUMENTS_S3_BUCKET created"
fi
