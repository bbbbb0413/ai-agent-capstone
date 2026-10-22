package com.opslens.backend.slack;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.opslens.backend.config.OpsLensProperties;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** webhook은 쓰기 전용 설정이다. tenant를 AAD로 묶어 다른 고객사로 암호문을 복사해도 복호화되지 않는다. */
@Component
public class SlackSecretBox {
    private final SecretKeySpec key;
    private final SecureRandom random = new SecureRandom();

    public SlackSecretBox(OpsLensProperties properties, @Value("${SLACK_ENCRYPTION_KEY:}") String secret) {
        try {
            String material = secret.isBlank() ? properties.jwtSecret() : secret;
            key = new SecretKeySpec(MessageDigest.getInstance("SHA-256")
                    .digest(("opslens:slack:v1:" + material).getBytes(UTF_8)), "AES");
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Slack 암호화 초기화 실패");
        }
    }

    public String encrypt(String tenant, String plain) {
        byte[] iv = new byte[12];
        random.nextBytes(iv);
        try {
            Cipher cipher = cipher(Cipher.ENCRYPT_MODE, tenant, iv);
            byte[] encrypted = cipher.doFinal(plain.getBytes(UTF_8));
            byte[] value = Arrays.copyOf(iv, iv.length + encrypted.length);
            System.arraycopy(encrypted, 0, value, iv.length, encrypted.length);
            return Base64.getEncoder().encodeToString(value);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Slack 연결 정보 암호화 실패");
        }
    }

    public String decrypt(String tenant, String value) {
        try {
            byte[] bytes = Base64.getDecoder().decode(value);
            return new String(cipher(Cipher.DECRYPT_MODE, tenant, Arrays.copyOf(bytes, 12))
                    .doFinal(Arrays.copyOfRange(bytes, 12, bytes.length)), UTF_8);
        } catch (GeneralSecurityException | IllegalArgumentException e) {
            throw new IllegalStateException("Slack 연결 정보를 다시 저장해야 한다");
        }
    }

    private Cipher cipher(int mode, String tenant, byte[] iv) throws GeneralSecurityException {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(mode, key, new GCMParameterSpec(128, iv));
        cipher.updateAAD(tenant.getBytes(UTF_8));
        return cipher;
    }
}
