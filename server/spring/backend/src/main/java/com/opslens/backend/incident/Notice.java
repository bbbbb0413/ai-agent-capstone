package com.opslens.backend.incident;

import java.time.OffsetDateTime;

/** 고객사에 발송된 공지. 실제 발송 대신 감사 로그(NOTICE_SENT)에 남긴 것을 포털에 보여 준다. */
public record Notice(String message, OffsetDateTime sentAt) {}
