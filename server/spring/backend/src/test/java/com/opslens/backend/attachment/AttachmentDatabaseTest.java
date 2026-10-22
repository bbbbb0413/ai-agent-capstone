package com.opslens.backend.attachment;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.settings.SettingsService;
import com.opslens.backend.task.*;
import com.opslens.backend.tenant.*;
import java.sql.*;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.*;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

@EnabledIfEnvironmentVariable(named="OPSLENS_TEST_DATABASE_URL",matches=".+")
class AttachmentDatabaseTest {
    String schema; JdbcClient jdbc; TaskAttachments files; TaskService service; TaskOutboxRelay relay;
    @BeforeEach void setup() throws Exception {
        String url=System.getenv("OPSLENS_TEST_DATABASE_URL");schema="attachment_"+UUID.randomUUID().toString().replace("-","");
        try(var c=DriverManager.getConnection(url,"postgres","")){c.createStatement().execute("CREATE SCHEMA "+schema);}
        var ds=new DriverManagerDataSource(url+"?currentSchema="+schema,"postgres","");jdbc=JdbcClient.create(ds);
        for(String version:List.of("V1__init","V5__task_summary","V12__task_outbox","V15__task_attachments"))
            new ResourceDatabasePopulator(new ClassPathResource("db/migration/"+version+".sql")).execute(ds);
        jdbc.sql("INSERT INTO tenants VALUES('tenant-a','A','GOLD',99,15,'http://local','{}')").update();
        var tenants=mock(TenantRepository.class);var tenant=mock(Tenant.class);when(tenant.slaTier()).thenReturn("GOLD");when(tenants.get("tenant-a")).thenReturn(tenant);
        files=new TaskAttachments(jdbc);relay=mock(TaskOutboxRelay.class);var json=new ObjectMapper();
        service=new TaskService(new TaskRepository(jdbc,json),tenants,mock(IncidentRepository.class),new TaskOutboxRepository(jdbc,json),relay,
            mock(AgentClient.class),mock(TaskEventHub.class),mock(AuditLog.class),mock(SettingsService.class),new DataSourceTransactionManager(ds),files);
    }
    @AfterEach void cleanup() throws Exception {
        try(var c=DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"),"postgres","")){c.createStatement().execute("DROP SCHEMA "+schema+" CASCADE");}
    }
    @Test void taskFilesAndDispatchAreCommittedTogetherWithoutBytesInQueue() {
        var id=service.create("tenant-a",null,"ATTACHMENT","analyze",null,List.of(new TaskAttachments.Prepared("error.log","text/plain","ERROR_DB_TIMEOUT".getBytes())));
        assertEquals(1,files.list(id).size());assertArrayEquals("ERROR_DB_TIMEOUT".getBytes(),files.contents(id).getFirst().content());
        assertEquals(1,jdbc.sql("SELECT count(*) FROM task_outbox").query(Integer.class).single());
        assertFalse(jdbc.sql("SELECT payload::text FROM task_outbox").query(String.class).single().contains("ERROR_DB_TIMEOUT"));
        verify(relay).kick(id);
    }
    @Test void attachmentFailureRollsBackTaskAndDoesNotDispatch() {
        assertThrows(Exception.class,()->service.create("tenant-a",null,"ATTACHMENT","analyze",null,List.of(new TaskAttachments.Prepared("empty.log","text/plain",new byte[0]))));
        assertEquals(0,jdbc.sql("SELECT count(*) FROM ai_tasks").query(Integer.class).single());
        assertEquals(0,jdbc.sql("SELECT count(*) FROM task_outbox").query(Integer.class).single());
        verifyNoInteractions(relay);
    }
}
