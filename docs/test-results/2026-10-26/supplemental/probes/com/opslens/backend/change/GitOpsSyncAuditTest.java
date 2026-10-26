package com.opslens.backend.change;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.task.*;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.config.OpsLensProperties;
import java.util.*;
import org.junit.jupiter.api.Test;
class GitOpsSyncAuditTest {
  void check(String revision, String health) throws Exception {
    var tasks=mock(TaskRepository.class);var service=mock(TaskService.class);var gitops=mock(GitOpsRepository.class);
    var task=mock(TaskRecord.class);var json=new ObjectMapper();var props=mock(OpsLensProperties.class);
    when(props.gitops()).thenReturn(new OpsLensProperties.Gitops("","","",java.time.Duration.ofMinutes(30)));
    when(task.id()).thenReturn(UUID.randomUUID());when(task.pendingAction()).thenReturn(json.readTree("{\"prNumber\":1}"));
    when(tasks.listChangePending("tenant-a")).thenReturn(List.of(task));
    when(gitops.pullRequest(1)).thenReturn(json.readTree("{\"merged\":true,\"merge_commit_sha\":\"expected123\"}"));
    when(service.finishChange(eq(task),eq("SYNCED"),anyString(),eq("argocd"))).thenReturn(true);
    var controller=new GitOpsSyncController(tasks,service,gitops,mock(AuditLog.class),props);
    var result=controller.syncEvent(new GitOpsSyncController.SyncEvent("tenant-a",revision,health));
    assertEquals(List.of(),result.get("resumed"),"Unsafe GitOps event must not resume task");
  }
  @Test void unrelatedRevisionMustNotResume() throws Exception {check("unrelated999","Healthy");}
  @Test void degradedHealthMustNotResume() throws Exception {check("expected123","Degraded");}
}
