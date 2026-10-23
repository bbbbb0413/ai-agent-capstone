package com.opslens.backend.document;

import java.net.URI;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;

/**
 * 문서 원본과 추출 본문을 S3 호환 저장소(SeaweedFS)에 둔다 (SPEC-document-store 3.1). 객체는 덮어쓰지 않는다. 버전마다 새 경로다.
 *   {tenant}/{docId}/v{version}/original.{ext}
 *   {tenant}/{docId}/v{version}/extracted.md
 */
@Component
public class DocumentStorage {

    private final S3Client s3;
    private final String bucket;

    public DocumentStorage(DocumentStoreProperties props) {
        this.bucket = props.bucket();
        this.s3 = S3Client.builder()
                .endpointOverride(URI.create(props.endpoint()))
                .region(Region.US_EAST_1)   // SeaweedFS는 지역을 보지 않지만 SDK가 요구한다
                .credentialsProvider(StaticCredentialsProvider.create(
                        AwsBasicCredentials.create(props.accessKey(), props.secretKey())))
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build())
                .httpClient(UrlConnectionHttpClient.create())
                .build();
    }

    static String originalKey(String tenantId, String docId, int version, String format) {
        return "%s/%s/v%d/original.%s".formatted(tenantId, docId, version, format);
    }

    static String extractedKey(String originalKey) {
        return originalKey.substring(0, originalKey.lastIndexOf('/') + 1) + "extracted.md";
    }

    public void put(String key, byte[] content, String contentType) {
        s3.putObject(b -> b.bucket(bucket).key(key).contentType(contentType), RequestBody.fromBytes(content));
    }

    public byte[] get(String key) {
        return s3.getObjectAsBytes(b -> b.bucket(bucket).key(key)).asByteArray();
    }

    public void copy(String from, String to) {
        s3.copyObject(b -> b.sourceBucket(bucket).sourceKey(from).destinationBucket(bucket).destinationKey(to));
    }
}
