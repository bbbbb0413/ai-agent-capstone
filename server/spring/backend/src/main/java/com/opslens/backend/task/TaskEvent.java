package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.OffsetDateTime;

/** 저장된 진행 이벤트 한 건. 화면의 진행 상황 타임라인이 쓴다. */
public record TaskEvent(String type, JsonNode data, OffsetDateTime createdAt) {}
