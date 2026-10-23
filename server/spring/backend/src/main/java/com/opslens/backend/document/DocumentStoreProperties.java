package com.opslens.backend.document;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 근거 문서 저장소 설정 (SPEC-document-store 3.1). S3 계정은 backend만 갖는다.
 *   endpoint   S3 호환 저장소(SeaweedFS) 주소
 *   bucket     문서 버킷. seaweedfs-init이 만든다
 *   seedDir    처음 데이터(data/documents). 비우거나 없으면 시드를 건너뛴다
 */
@ConfigurationProperties("opslens.documents")
public record DocumentStoreProperties(String endpoint, String bucket, String accessKey, String secretKey,
        String seedDir) {}
