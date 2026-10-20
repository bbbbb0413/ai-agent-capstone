package com.opslens.backend.tenant;

import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

@Repository
public class TenantRepository {

    public record Contact(String username, String role) {}

    private final JdbcClient jdbc;

    public TenantRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Tenant> find(String id) {
        return jdbc.sql("SELECT * FROM tenants WHERE id = ?").param(id).query(TenantRepository::toTenant).optional();
    }

    public Tenant get(String id) {
        return find(id).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown tenant " + id));
    }

    public List<Tenant> findAll(List<String> ids) {
        return jdbc.sql("SELECT * FROM tenants WHERE id = ANY(?) ORDER BY id")
                .param(ids.toArray(String[]::new))
                .query(TenantRepository::toTenant).list();
    }

    /** 에스컬레이션 대상: 담당 엔지니어와 MSP 관리자 */
    public List<Contact> oncall(String tenantId) {
        return jdbc.sql("""
                        SELECT u.username, u.role FROM users u
                        WHERE u.role = 'MSP_ADMIN'
                           OR u.id IN (SELECT user_id FROM user_tenants WHERE tenant_id = ?)
                        ORDER BY u.role DESC, u.username
                        """)
                .param(tenantId)
                .query((rs, n) -> new Contact(rs.getString("username"), rs.getString("role")))
                .list();
    }

    /** 계약상 승인이 필요한 조치를 요청할 고객사 담당 (에스컬레이션 대상) */
    public Optional<String> customerContact(String tenantId) {
        return jdbc.sql("SELECT customer_contact FROM tenants WHERE id = ?").param(tenantId)
                .query(String.class).optional();
    }

    private static Tenant toTenant(ResultSet rs, int rowNum) throws SQLException {
        return new Tenant(rs.getString("id"), rs.getString("name"), rs.getString("sla_tier"),
                rs.getBigDecimal("availability_target"), rs.getInt("response_minutes"),
                strings(rs.getArray("allowed_actions")), rs.getString("change_mode"), rs.getString("namespace"),
                rs.getString("gitops_path"));
    }

    private static List<String> strings(Array array) throws SQLException {
        return array == null ? List.of() : Arrays.asList((String[]) array.getArray());
    }
}
