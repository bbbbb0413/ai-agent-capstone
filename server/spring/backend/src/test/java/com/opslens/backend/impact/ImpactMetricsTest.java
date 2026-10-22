package com.opslens.backend.impact;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.opslens.backend.config.OpsLensProperties;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

class ImpactMetricsTest {
    @Test void scopesAllFiveQueriesAndDropsForeignNonfiniteAndNegativeValues() throws Exception {
        var queries = new ArrayList<String>();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/query", exchange -> {
            queries.add(URLDecoder.decode(exchange.getRequestURI().getRawQuery(), StandardCharsets.UTF_8));
            byte[] body = """
                {"status":"success","data":{"resultType":"vector","result":[
                  {"metric":{"tenant":"tenant-a","service":"order"},"value":[1,"10"]},
                  {"metric":{"tenant":"tenant-b","service":"secret"},"value":[1,"7"]},
                  {"metric":{"tenant":"tenant-a","service":"missing"},"value":[1,"NaN"]},
                  {"metric":{"tenant":"tenant-a","service":"negative"},"value":[1,"-1"]}
                ]}}
                """.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (var output = exchange.getResponseBody()) { output.write(body); }
        });
        server.start();
        try {
            var props = mock(OpsLensProperties.class);
            when(props.prometheusUrl()).thenReturn("http://127.0.0.1:" + server.getAddress().getPort());
            var snapshot = new ImpactMetrics(RestClient.builder(), props).read("tenant-a", Instant.parse("2026-10-22T00:00:00Z"));
            assertEquals(5, queries.size());
            assertTrue(queries.stream().allMatch(q -> q.contains("tenant=\"tenant-a\"") && q.contains("time=2026-10-22T00:00:00Z")));
            assertTrue(queries.get(1).contains("or on (tenant,service)"));
            assertEquals(Map.of(new ImpactMetrics.Key("order", ""), 10.0), snapshot.requests());
            assertEquals(1, snapshot.dependencyLatency().size());
            assertTrue(snapshot.warnings().isEmpty());
        } finally { server.stop(0); }
    }

    @Test void monitoringFailureReturnsUnknownDataAndWarningInsteadOfHealthyZero() throws Exception {
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/query", exchange -> { exchange.sendResponseHeaders(503, -1); exchange.close(); });
        server.start();
        try {
            var props = mock(OpsLensProperties.class);
            when(props.prometheusUrl()).thenReturn("http://127.0.0.1:" + server.getAddress().getPort());
            var snapshot = new ImpactMetrics(RestClient.builder(), props).read("tenant-a", Instant.now());
            assertTrue(snapshot.errors().isEmpty());
            assertTrue(snapshot.latency().isEmpty());
            assertFalse(snapshot.warnings().isEmpty());
        } finally { server.stop(0); }
    }

    @Test void escapesPrometheusLabelValues() {
        assertEquals("a\\\"b\\\\c\\nd", ImpactMetrics.escape("a\"b\\c\nd"));
    }
}
