import re

import psycopg2
from psycopg2.extras import RealDictCursor

from app.core.config import settings


DEFAULT_QUERY_TIMEOUT = 10
MAX_RESULT_ROWS = 100
READ_ONLY_SQL_PREFIXES = ("SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE")
FORBIDDEN_SQL_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "CLUSTER",
    "COMMENT",
    "SECURITY",
    "SET",
    "RESET",
    "PREPARE",
    "EXECUTE",
    "DEALLOCATE",
    "LOAD",
    "LOCK",
    "UNLOCK",
    "CHECKPOINT",
)


def get_connection():
    return psycopg2.connect(
        host=settings.YUGABYTE_HOST,
        port=settings.YUGABYTE_PORT,
        database=settings.YUGABYTE_DATABASE,
        user=settings.YUGABYTE_USER,
        password=settings.YUGABYTE_PASSWORD,
        connect_timeout=5,
    )


def _validate_read_only_sql(sql: str) -> tuple[bool, str | None]:
    """Validate that SQL is read-only. Returns (is_valid, error_message)."""
    stripped = sql.strip().upper()
    if not stripped:
        return False, "Empty SQL query"

    first_word = stripped.split()[0] if stripped.split() else ""
    if first_word not in READ_ONLY_SQL_PREFIXES:
        return False, f"Only read-only queries allowed. Query starts with: {first_word}"

    # Match forbidden keywords as whole words only — substring matching
    # false-positives on common identifiers (e.g. CREATE inside created_at).
    for keyword in FORBIDDEN_SQL_KEYWORDS:
        if re.search(r"\b" + re.escape(keyword) + r"\b", stripped):
            return False, f"Forbidden keyword detected: {keyword}"

    return True, None


def _serialize_data(data):
    """Convert datetime/date objects to ISO format strings for JSON serialization."""
    import datetime
    if isinstance(data, list):
        return [_serialize_data(item) for item in data]
    elif isinstance(data, dict):
        return {k: _serialize_data(v) for k, v in data.items()}
    elif isinstance(data, (datetime.datetime, datetime.date)):
        return data.isoformat()
    elif isinstance(data, (datetime.time,)):
        return data.isoformat()
    return data


def _execute_with_timeout(sql: str, params: tuple = (), timeout: int = DEFAULT_QUERY_TIMEOUT, limit: int = MAX_RESULT_ROWS):
    """Execute a read-only query with timeout and result limit."""
    is_valid, error = _validate_read_only_sql(sql)
    if not is_valid:
        return {"success": False, "error": error}

    limited_sql = sql.strip().rstrip(";")
    if "LIMIT" not in limited_sql.upper():
        limited_sql += f" LIMIT {limit}"

    try:
        conn = get_connection()
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SET LOCAL statement_timeout = {timeout * 1000}")
            cur.execute(limited_sql, params)
            if cur.description:
                data = cur.fetchall()
            else:
                data = {"affected_rows": cur.rowcount}
        conn.close()
        return {"success": True, "data": _serialize_data(data)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def health():
    try:
        conn = get_connection()
        conn.close()
        return {"success": True, "status": "connected"}
    except Exception as e:
        out = {"success": False, "error": str(e)}
        try:
            from app.services import containers

            state = containers.container_state("yugabyte")
            if state.get("success") and state.get("running"):
                out["hint"] = (
                    "Database pod is Running in Kubernetes but unreachable "
                    "from the backend — the kubectl port-forward is down "
                    "(it dies when the StatefulSet scales to 0). Restart "
                    "it, then re-check health. Do NOT use docker start."
                )
                out["port_forward"] = (
                    "kubectl port-forward -n databases svc/yugabytedb 5433:5433"
                )
        except Exception:
            pass
        return out


def execute(sql: str):
    return _execute_with_timeout(sql)


def query(sql: str):
    return _execute_with_timeout(sql)


def insert(table: str, data: dict):
    columns = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    values = tuple(data.values())
    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *"
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, values)
            result = cur.fetchone()
            conn.commit()
        conn.close()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def update(table: str, data: dict, where: dict):
    set_clause = ", ".join([f"{k} = %s" for k in data.keys()])
    where_clause = " AND ".join([f"{k} = %s" for k in where.keys()])
    values = tuple(list(data.values()) + list(where.values()))
    sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause} RETURNING *"
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, values)
            result = cur.fetchone()
            conn.commit()
        conn.close()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete(table: str, where: dict):
    where_clause = " AND ".join([f"{k} = %s" for k in where.keys()])
    values = tuple(where.values())
    sql = f"DELETE FROM {table} WHERE {where_clause} RETURNING *"
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, values)
            result = cur.fetchone()
            conn.commit()
        conn.close()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ===================================================================
# Database Investigation Functions (Read-Only)
# ===================================================================

def cluster_health():
    """Get YugabyteDB cluster health: node status, replication, connections."""
    sql = """
    SELECT
        host,
        port,
        node_type,
        cloud,
        region,
        zone,
        public_ip,
        uuid,
        universe_uuid,
        num_connections
    FROM yb_servers()
    ORDER BY host
    """
    return _execute_with_timeout(sql, limit=50)


def connection_status():
    """Get active connections and connection statistics."""
    sql = """
    SELECT
        count(*) as total_connections,
        count(*) FILTER (WHERE state = 'active') as active_connections,
        count(*) FILTER (WHERE state = 'idle') as idle_connections,
        count(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction,
        count(*) FILTER (WHERE wait_event_type IS NOT NULL) as waiting_connections,
        max(backend_start) as oldest_connection,
        max(state_change) as latest_state_change
    FROM pg_stat_activity
    WHERE datname = current_database()
    """
    return _execute_with_timeout(sql)


def slow_queries(limit: int = 20):
    """Get slow queries from pg_stat_statements.

    Keeps the column names stable by aliasing: newer PostgreSQL exposes
    `total_exec_time`/`mean_exec_time` while older builds (incl. this
    YugabyteDB version) only expose `total_time`/`mean_time`. If the modern
    variant fails, retry with the legacy names aliased back to the same keys.
    """
    modern_sql = """
    SELECT
        queryid,
        calls,
        total_exec_time,
        mean_exec_time,
        stddev_exec_time,
        rows,
        shared_blks_hit,
        shared_blks_read,
        shared_blks_written,
        local_blks_hit,
        local_blks_read,
        local_blks_written,
        temp_blks_read,
        temp_blks_written,
        LEFT(query, 500) as query_preview
    FROM pg_stat_statements
    WHERE calls > 0
    ORDER BY mean_exec_time DESC
    LIMIT %s
    """
    legacy_sql = """
    SELECT
        queryid,
        calls,
        total_time as total_exec_time,
        mean_time as mean_exec_time,
        stddev_time as stddev_exec_time,
        rows,
        shared_blks_hit,
        shared_blks_read,
        shared_blks_written,
        local_blks_hit,
        local_blks_read,
        local_blks_written,
        temp_blks_read,
        temp_blks_written,
        LEFT(query, 500) as query_preview
    FROM pg_stat_statements
    WHERE calls > 0
    ORDER BY mean_time DESC
    LIMIT %s
    """
    result = _execute_with_timeout(modern_sql, params=(limit,), limit=limit)
    if result.get("success") or "total_exec_time" not in (result.get("error") or ""):
        return result
    return _execute_with_timeout(legacy_sql, params=(limit,), limit=limit)


def recent_errors(limit: int = 50):
    """Get recent database errors from pg_stat_activity and logs."""
    sql = """
    SELECT
        pid,
        usename,
        application_name,
        client_addr,
        state,
        wait_event_type,
        wait_event,
        query_start,
        state_change,
        LEFT(query, 500) as query_preview
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND (state = 'active' OR wait_event_type IS NOT NULL)
    ORDER BY query_start ASC
    LIMIT %s
    """
    return _execute_with_timeout(sql, params=(limit,), limit=limit)


def schema_info(schema: str = "public"):
    """Get schema/table information: tables, columns, constraints, indexes."""
    tables_sql = """
    SELECT
        t.table_name,
        t.table_type,
        pg_total_relation_size(c.oid) as total_size_bytes,
        pg_relation_size(c.oid) as table_size_bytes
    FROM information_schema.tables t
    JOIN pg_class c ON c.relname = t.table_name
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE t.table_schema = %s
      AND n.nspname = %s
    ORDER BY t.table_name
    """
    tables_result = _execute_with_timeout(tables_sql, params=(schema, schema), limit=100)
    if not tables_result.get("success"):
        return tables_result

    tables = tables_result.get("data", [])
    if not tables:
        return {"success": True, "data": {"tables": [], "schema": schema}}

    columns_sql = """
    SELECT
        table_name,
        column_name,
        data_type,
        is_nullable,
        column_default,
        character_maximum_length,
        numeric_precision,
        numeric_scale,
        ordinal_position
    FROM information_schema.columns
    WHERE table_schema = %s
    ORDER BY table_name, ordinal_position
    """
    columns_result = _execute_with_timeout(columns_sql, params=(schema,), limit=500)

    constraints_sql = """
    SELECT
        tc.table_name,
        tc.constraint_name,
        tc.constraint_type,
        kcu.column_name,
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    LEFT JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
    WHERE tc.table_schema = %s
    ORDER BY tc.table_name, tc.constraint_name
    """
    constraints_result = _execute_with_timeout(constraints_sql, params=(schema,), limit=200)

    indexes_sql = """
    SELECT
        schemaname,
        tablename,
        indexname,
        indexdef
    FROM pg_indexes
    WHERE schemaname = %s
    ORDER BY tablename, indexname
    """
    indexes_result = _execute_with_timeout(indexes_sql, params=(schema,), limit=200)

    return {
        "success": True,
        "data": {
            "schema": schema,
            "tables": tables,
            "columns": columns_result.get("data", []) if columns_result.get("success") else [],
            "constraints": constraints_result.get("data", []) if constraints_result.get("success") else [],
            "indexes": indexes_result.get("data", []) if indexes_result.get("success") else [],
        },
    }


def record_inspection(table: str, where: dict | None = None, limit: int = 50, schema: str = "public"):
    """Safely inspect records from a table with optional filtering."""
    if not table or not table.replace("_", "").isalnum():
        return {"success": False, "error": "Invalid table name"}

    where_clause = ""
    params = []
    if where:
        conditions = []
        for k, v in where.items():
            if not k.replace("_", "").isalnum():
                return {"success": False, "error": f"Invalid column name: {k}"}
            conditions.append(f"{k} = %s")
            params.append(v)
        where_clause = "WHERE " + " AND ".join(conditions)

    sql = f"SELECT * FROM {schema}.{table} {where_clause}"
    params.append(limit)
    return _execute_with_timeout(sql, params=tuple(params), limit=limit)


def data_integrity_checks(schema: str = "public", limit: int = 100):
    """Run data integrity checks: duplicates, NULLs in required fields, constraint violations."""
    results = {}

    # Check for tables with primary keys and find potential duplicates
    pk_sql = """
    SELECT
        tc.table_name,
        kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    WHERE tc.table_schema = %s
      AND tc.constraint_type = 'PRIMARY KEY'
    """
    pk_result = _execute_with_timeout(pk_sql, params=(schema,), limit=100)
    if pk_result.get("success") and pk_result.get("data"):
        duplicate_checks = []
        for pk in pk_result["data"]:
            table = pk["table_name"]
            col = pk["column_name"]
            dup_sql = f"""
            SELECT {col}, count(*) as cnt
            FROM {schema}.{table}
            GROUP BY {col}
            HAVING count(*) > 1
            LIMIT %s
            """
            dup_result = _execute_with_timeout(dup_sql, params=(limit,), limit=limit)
            if dup_result.get("success") and dup_result.get("data"):
                duplicate_checks.append({"table": table, "column": col, "duplicates": dup_result["data"]})
        results["primary_key_duplicates"] = duplicate_checks

    # Check for NULLs in NOT NULL columns (should be none, but verify)
    not_null_sql = """
    SELECT
        table_name,
        column_name
    FROM information_schema.columns
    WHERE table_schema = %s
      AND is_nullable = 'NO'
      AND column_default IS NULL
    """
    nn_result = _execute_with_timeout(not_null_sql, params=(schema,), limit=200)
    if nn_result.get("success") and nn_result.get("data"):
        null_checks = []
        for col_info in nn_result["data"][:20]:  # Limit checks to avoid too many queries
            table = col_info["table_name"]
            col = col_info["column_name"]
            null_sql = f"SELECT count(*) as null_count FROM {schema}.{table} WHERE {col} IS NULL"
            null_result = _execute_with_timeout(null_sql, limit=1)
            if null_result.get("success") and null_result.get("data"):
                count = null_result["data"][0].get("null_count", 0)
                if count > 0:
                    null_checks.append({"table": table, "column": col, "null_count": count})
        results["unexpected_nulls"] = null_checks

    # Check for constraint violations (foreign keys)
    fk_sql = """
    SELECT
        tc.table_name,
        kcu.column_name,
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
    WHERE tc.table_schema = %s
      AND tc.constraint_type = 'FOREIGN KEY'
    """
    fk_result = _execute_with_timeout(fk_sql, params=(schema,), limit=50)
    if fk_result.get("success") and fk_result.get("data"):
        fk_violations = []
        for fk in fk_result["data"][:10]:
            table = fk["table_name"]
            col = fk["column_name"]
            fk_table = fk["foreign_table_name"]
            fk_col = fk["foreign_column_name"]
            viol_sql = f"""
            SELECT count(*) as violations
            FROM {schema}.{table} t
            LEFT JOIN {schema}.{fk_table} f ON t.{col} = f.{fk_col}
            WHERE t.{col} IS NOT NULL AND f.{fk_col} IS NULL
            """
            viol_result = _execute_with_timeout(viol_sql, limit=1)
            if viol_result.get("success") and viol_result.get("data"):
                count = viol_result["data"][0].get("violations", 0)
                if count > 0:
                    fk_violations.append({
                        "table": table,
                        "column": col,
                        "referenced_table": fk_table,
                        "referenced_column": fk_col,
                        "violations": count
                    })
        results["foreign_key_violations"] = fk_violations

    return {"success": True, "data": results}


def table_statistics(schema: str = "public"):
    """Get table statistics: row counts, sizes, dead tuples."""
    sql = """
    SELECT
        schemaname,
        relname as table_name,
        n_live_tup as live_tuples,
        n_dead_tup as dead_tuples,
        pg_total_relation_size(relid) as total_size_bytes,
        pg_relation_size(relid) as table_size_bytes,
        pg_indexes_size(relid) as indexes_size_bytes
    FROM pg_stat_user_tables
    WHERE schemaname = %s
    ORDER BY n_live_tup DESC
    """
    return _execute_with_timeout(sql, params=(schema,), limit=100)


def replication_status():
    """Get YugabyteDB-specific replication status."""
    # yb_tablet_replication_status doesn't exist in all versions, use alternative
    sql = """
    SELECT
        t.table_name,
        t.table_type,
        pg_total_relation_size(c.oid) as total_size_bytes
    FROM information_schema.tables t
    JOIN pg_class c ON c.relname = t.table_name
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE t.table_schema = 'public'
      AND n.nspname = 'public'
    ORDER BY pg_total_relation_size(c.oid) DESC
    LIMIT %s
    """
    return _execute_with_timeout(sql, params=(50,), limit=50)


# ===================================================================
# Demo/Debug functions (allows mutations - use with caution)
# ===================================================================

def execute_raw(sql: str, params: tuple = ()):
    """Execute raw SQL with mutations allowed. FOR DEMO/DEBUG USE ONLY."""
    try:
        conn = get_connection()
        conn.set_session(autocommit=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SET LOCAL statement_timeout = {10 * 1000}")
            cur.execute(sql, params)
            if cur.description:
                data = cur.fetchall()
            else:
                data = {"affected_rows": cur.rowcount}
        conn.close()
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}