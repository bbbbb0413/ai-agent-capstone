package com.opslens.backend.slack;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

final class SlackSignature {
    static boolean valid(String secret,String timestamp,String signature,byte[] body,Instant now) {
        try {
            long seconds=Long.parseLong(timestamp);
            if (seconds<now.getEpochSecond()-300 || seconds>now.getEpochSecond()+300 || body.length>65536
                    || signature==null || !signature.matches("v0=[a-f0-9]{64}")) return false;
            Mac mac=Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
            mac.update(("v0:"+timestamp+":").getBytes(StandardCharsets.UTF_8));
            String expected="v0="+HexFormat.of().formatHex(mac.doFinal(body));
            return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),signature.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) { return false; }
    }
}
