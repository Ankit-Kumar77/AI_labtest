import aerospike
from aerospike import exception as aerospike_exception
from aerospike import predicates as p

from app.core.config import settings


MAX_SCAN_RECORDS = 1000
DEFAULT_SCAN_LIMIT = 100


def get_client():
    config = {
        "hosts": [
            (h.split(":")[0], int(h.split(":")[1])) for h in settings.AEROSPIKE_HOSTS.split(",")
        ],
    }
    return aerospike.client(config).connect()


def health():
    try:
        client = get_client()
        client.close()
        return {"success": True, "status": "connected"}
    except Exception as e:
        out = {"success": False, "error": str(e)}
        hint = _port_forward_hint("aerospike")
        if hint:
            out["hint"] = hint["hint"]
            out["port_forward"] = hint["port_forward"]
        return out


def _port_forward_hint(target: str):
    """Detect the 'pod Running but backend unreachable' case.

    The backend reaches K8s databases via `kubectl port-forward`, which
    dies whenever the StatefulSet scales to 0 (chaos down/fail). After a
    recover the pod is Running again but the forward is gone, so health
    stays red until the forward is restarted. Surface that explicitly.
    """
    try:
        from app.services import containers

        state = containers.container_state(target)
    except Exception:
        return None
    if state.get("success") and state.get("running"):
        cmd = (
            "kubectl port-forward -n databases svc/aerospike 3001:3000"
            if target == "aerospike"
            else "kubectl port-forward -n databases svc/yugabytedb 5433:5433"
        )
        return {
            "hint": (
                "Database pod is Running in Kubernetes but unreachable "
                "from the backend — the kubectl port-forward is down "
                "(it dies when the StatefulSet scales to 0). Restart it, "
                "then re-check health. Do NOT use docker start."
            ),
            "port_forward": cmd,
        }
    return None


def query(namespace: str, set_name: str, key: str):
    try:
        client = get_client()
        key_tuple = (namespace, set_name, key)
        _, _, bins = client.get(key_tuple)
        client.close()
        return {"success": True, "data": bins}
    except aerospike_exception.RecordNotFound:
        return {"success": False, "error": "Record not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def write(namespace: str, set_name: str, key: str, bins: dict):
    try:
        client = get_client()
        key_tuple = (namespace, set_name, key)
        bins = dict(bins)
        bins["_key"] = key
        client.put(key_tuple, bins)
        client.close()
        return {"success": True, "message": "Record written"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete(namespace: str, set_name: str, key: str):
    try:
        client = get_client()
        key_tuple = (namespace, set_name, key)
        client.remove(key_tuple)
        client.close()
        return {"success": True, "message": "Record deleted"}
    except aerospike_exception.RecordNotFound:
        return {"success": False, "error": "Record not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def scan(namespace: str, set_name: str):
    try:
        client = get_client()
        records = []
        scan = client.scan(namespace, set_name)
        scan.foreach(
            lambda r: records.append(
                {
                    "key": r[2].get("_key") or r[0][2] or "",
                    "bins": {k: v for k, v in r[2].items() if k != "_key"},
                }
            )
        )
        client.close()
        return {"success": True, "data": records}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ===================================================================
# Database Investigation Functions (Read-Only)
# ===================================================================

def _parse_info_stats(stats_str):
    """Parse Aerospike info statistics string into dict."""
    if not stats_str:
        return {}
    stats = {}
    for part in stats_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            stats[k] = v
    return stats


def _get_node_names(client):
    """Get node names from the client."""
    try:
        info = client.info_all("node")
        node_names = []
        for node_name, data in info.items():
            if data and len(data) > 1 and data[1]:
                node_names.append(node_name)
        return node_names
    except Exception:
        return []


def cluster_health():
    """Get Aerospike cluster health: node status, cluster size, migrations."""
    try:
        client = get_client()
        
        # Get statistics from all nodes
        stats_info = client.info_all("statistics")
        
        node_info = []
        for node_name, data in stats_info.items():
            if data and len(data) > 1 and data[1]:
                stats = _parse_info_stats(data[1])
                node_info.append({
                    "node_name": node_name,
                    "status": "active",
                    "client_connections": stats.get("client_connections"),
                    "heartbeat_received_self": stats.get("heartbeat_received_self"),
                    "migrations_partition": stats.get("migrations_partition"),
                    "migrations_current": stats.get("migrations_current"),
                    "migrations_num_incoming": stats.get("migrations_num_incoming"),
                    "migrations_num_outgoing": stats.get("migrations_num_outgoing"),
                    "cluster_size": stats.get("cluster_size"),
                    "cluster_key": stats.get("cluster_key"),
                    "uptime": stats.get("uptime"),
                    "objects": stats.get("objects"),
                })
            else:
                node_info.append({
                    "node_name": node_name,
                    "status": "error",
                    "error": "No statistics data"
                })
        
        client.close()
        
        # Get cluster-level stats from first node
        cluster_stats = {}
        if node_info:
            first_node = node_info[0]
            cluster_stats = {
                "cluster_key": first_node.get("cluster_key"),
                "cluster_size": first_node.get("cluster_size"),
                "migrations_partition": first_node.get("migrations_partition"),
                "migrations_current": first_node.get("migrations_current"),
            }
        
        return {
            "success": True,
            "data": {
                "cluster_size": len([n for n in node_info if n.get("status") == "active"]),
                "nodes": node_info,
                "cluster_stats": cluster_stats
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def namespace_info(namespace: str | None = None):
    """Get namespace/set information: storage, records, sets."""
    try:
        client = get_client()
        
        if namespace:
            namespaces = [namespace]
        else:
            # Get all namespaces from info
            info_result = client.info_all("namespaces")
            namespaces = []
            for node_name, ns_tuple in info_result.items():
                if ns_tuple and len(ns_tuple) > 1 and ns_tuple[1]:
                    namespaces.extend([n.strip() for n in ns_tuple[1].split("\n") if n.strip()])
            namespaces = list(set(namespaces))
        
        ns_info = {}
        for ns in namespaces:
            try:
                # Get namespace stats
                stats_cmd = f"namespace/{ns}"
                ns_stats_result = client.info_all(stats_cmd)
                
                ns_data = {}
                for node_name, stat_tuple in ns_stats_result.items():
                    if stat_tuple and len(stat_tuple) > 1 and stat_tuple[1]:
                        # Parse stats string like "key1=val1;key2=val2"
                        stats = {}
                        for part in stat_tuple[1].split(";"):
                            if "=" in part:
                                k, v = part.split("=", 1)
                                stats[k] = v
                        ns_data[node_name] = stats
                
                # Get sets in namespace
                sets_cmd = f"sets/{ns}"
                sets_result = client.info_all(sets_cmd)
                sets = {}
                for node_name, set_tuple in sets_result.items():
                    if set_tuple and len(set_tuple) > 1 and set_tuple[1]:
                        # Sets format: ns=test:set=demo:objects=19:tombstones=0:...
                        # Each set entry is separated by newline, fields by colon
                        for line in set_tuple[1].split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            # Parse: ns=test:set=demo:objects=19:...
                            parts = line.split(":")
                            set_name = None
                            for part in parts:
                                if part.startswith("set="):
                                    set_name = part.split("=", 1)[1]
                                    break
                            if set_name:
                                sets[set_name] = line
                
                ns_info[ns] = {
                    "stats": ns_data,
                    "sets": list(sets.keys())
                }
            except Exception as e:
                ns_info[ns] = {"error": str(e)}
        
        client.close()
        return {"success": True, "data": ns_info}
    except Exception as e:
        return {"success": False, "error": str(e)}


def record_inspection(namespace: str, set_name: str, key: str | None = None, 
                      filters: dict | None = None, limit: int = DEFAULT_SCAN_LIMIT):
    """Inspect records with optional key or filters."""
    try:
        client = get_client()
        
        if key:
            # Single key lookup
            try:
                key_tuple = (namespace, set_name, key)
                _, _, bins = client.get(key_tuple)
                client.close()
                return {"success": True, "data": [{"key": key, "bins": bins}]}
            except aerospike_exception.RecordNotFound:
                client.close()
                return {"success": True, "data": []}
            except Exception as e:
                client.close()
                return {"success": False, "error": str(e)}
        
        # Scan with optional filters
        records = []
        scan = client.scan(namespace, set_name)
        
        if filters:
            predicate_list = []
            for k, v in filters.items():
                if isinstance(v, (int, float)):
                    predicate_list.append(p.equals(k, v))
                else:
                    predicate_list.append(p.equals(k, str(v)))
            scan.select(*[k for k in filters.keys()]) if filters else None
            scan.foreach(
                lambda r: records.append({
                    "key": r[2].get("_key") or r[0][2] or "",
                    "bins": {k: v for k, v in r[2].items() if k != "_key"},
                }) if len(records) < limit else None
            )
        else:
            scan.foreach(
                lambda r: records.append({
                    "key": r[2].get("_key") or r[0][2] or "",
                    "bins": {k: v for k, v in r[2].items() if k != "_key"},
                }) if len(records) < limit else None
            )
        
        client.close()
        return {"success": True, "data": records[:limit]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def operation_errors(limit: int = 50):
    """Get recent operation errors and connection issues from node stats."""
    try:
        client = get_client()
        
        # Get statistics from all nodes
        stats_info = client.info_all("statistics")
        
        error_summary = {
            "nodes_checked": 0,
            "connection_errors": [],
            "timeouts": [],
            "client_errors": [],
            "server_errors": []
        }
        
        for node_name, data in stats_info.items():
            if not data or len(data) <= 1 or not data[1]:
                error_summary["server_errors"].append({
                    "node": node_name,
                    "issue": "Failed to collect stats",
                    "error": "No statistics data"
                })
                continue
                
            error_summary["nodes_checked"] += 1
            stats = _parse_info_stats(data[1])
            
            # Check for various error counters
            if stats.get("client_connections", "0") == "0":
                error_summary["connection_errors"].append({
                    "node": node_name,
                    "issue": "No client connections",
                    "details": "Node may be unreachable or not accepting connections"
                })
            
            # Migration issues
            migrations_incoming = int(stats.get("migrations_num_incoming", "0"))
            migrations_outgoing = int(stats.get("migrations_num_outgoing", "0"))
            if migrations_incoming > 0 or migrations_outgoing > 0:
                error_summary["server_errors"].append({
                    "node": node_name,
                    "issue": "Active migrations",
                    "incoming": migrations_incoming,
                    "outgoing": migrations_outgoing
                })
            
            # Transaction errors
            txn_errors = int(stats.get("txn_errors", "0"))
            if txn_errors > 0:
                error_summary["server_errors"].append({
                    "node": node_name,
                    "issue": "Transaction errors",
                    "count": txn_errors
                })
            
            # Proxy errors (connection refused)
            proxy_errors = int(stats.get("proxy_errors", "0"))
            if proxy_errors > 0:
                error_summary["connection_errors"].append({
                    "node": node_name,
                    "issue": "Proxy/connection errors",
                    "count": proxy_errors
                })
            
            # Timeout errors
            timeout_errors = int(stats.get("timeout_errors", "0"))
            if timeout_errors > 0:
                error_summary["timeouts"].append({
                    "node": node_name,
                    "issue": "Timeout errors",
                    "count": timeout_errors
                })
        
        client.close()
        return {"success": True, "data": error_summary}
    except Exception as e:
        return {"success": False, "error": str(e)}


def data_integrity_checks(namespace: str, set_name: str, 
                          required_fields: list[str] | None = None,
                          unique_fields: list[str] | None = None,
                          limit: int = DEFAULT_SCAN_LIMIT):
    """Check data integrity: missing required fields, duplicate logical records."""
    try:
        client = get_client()
        
        results = {
            "namespace": namespace,
            "set": set_name,
            "missing_required_fields": [],
            "duplicate_logical_records": [],
            "invalid_field_values": [],
            "records_scanned": 0
        }
        
        if not required_fields and not unique_fields:
            client.close()
            return {"success": True, "data": results}
        
        records_seen = {}
        missing_counts = {field: 0 for field in (required_fields or [])}
        invalid_counts = {field: 0 for field in (required_fields or [])}
        
        scan = client.scan(namespace, set_name)
        
        def check_record(r):
            nonlocal results
            if results["records_scanned"] >= limit:
                return
            
            bins = r[2]
            key = bins.get("_key") or r[0][2] or ""
            results["records_scanned"] += 1
            
            # Check required fields
            if required_fields:
                for field in required_fields:
                    if field not in bins or bins[field] is None or bins[field] == "":
                        missing_counts[field] += 1
                        if missing_counts[field] <= 5:  # Limit examples
                            results["missing_required_fields"].append({
                                "key": key,
                                "field": field,
                                "issue": "Missing or empty required field"
                            })
            
            # Check for invalid values (e.g., negative numbers where positive expected)
            if required_fields:
                for field in required_fields:
                    if field in bins and isinstance(bins[field], (int, float)):
                        if bins[field] < 0 and field.endswith(("_count", "_id", "_size", "_amount", "age")):
                            invalid_counts[field] += 1
                            if invalid_counts[field] <= 5:
                                results["invalid_field_values"].append({
                                    "key": key,
                                    "field": field,
                                    "value": bins[field],
                                    "issue": "Potentially invalid negative value"
                                })
            
            # Check unique fields for duplicates
            if unique_fields:
                for field in unique_fields:
                    if field in bins:
                        val = bins[field]
                        if val in records_seen:
                            results["duplicate_logical_records"].append({
                                "field": field,
                                "value": val,
                                "keys": [records_seen[val], key],
                                "issue": f"Duplicate value for unique field '{field}'"
                            })
                        else:
                            records_seen[val] = key
        
        scan.foreach(check_record)
        client.close()
        
        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "error": str(e)}


def namespace_set_stats(namespace: str, set_name: str):
    """Get detailed stats for a specific namespace/set."""
    try:
        client = get_client()
        
        info_cmd = f"sets/{namespace}/{set_name}"
        result = client.info_all(info_cmd)
        
        stats = {}
        for node_name, set_tuple in result.items():
            if set_tuple and len(set_tuple) > 1 and set_tuple[1]:
                # Parse format: ns=test:set=demo:objects=19:tombstones=0:...
                node_stats = {}
                for part in set_tuple[1].split(":"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        node_stats[k] = v
                stats[node_name] = node_stats
        
        client.close()
        return {"success": True, "data": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


def latency_info():
    """Get latency-related information from node stats."""
    try:
        client = get_client()
        
        # Get statistics from all nodes
        stats_info = client.info_all("statistics")
        
        latency_data = []
        for node_name, data in stats_info.items():
            if not data or len(data) <= 1 or not data[1]:
                latency_data.append({"node": node_name, "error": "No statistics data"})
                continue
            
            stats = _parse_info_stats(data[1])
            
            def get_stat(key, default=0):
                try:
                    return int(stats.get(key, default))
                except (ValueError, TypeError):
                    return default
            
            latency_data.append({
                "node": node_name,
                "read_latency_gt_1ms": get_stat("read_latency_gt_1ms"),
                "read_latency_gt_4ms": get_stat("read_latency_gt_4ms"),
                "read_latency_gt_8ms": get_stat("read_latency_gt_8ms"),
                "read_latency_gt_16ms": get_stat("read_latency_gt_16ms"),
                "read_latency_gt_32ms": get_stat("read_latency_gt_32ms"),
                "read_latency_gt_64ms": get_stat("read_latency_gt_64ms"),
                "read_latency_gt_128ms": get_stat("read_latency_gt_128ms"),
                "write_latency_gt_1ms": get_stat("write_latency_gt_1ms"),
                "write_latency_gt_4ms": get_stat("write_latency_gt_4ms"),
                "write_latency_gt_8ms": get_stat("write_latency_gt_8ms"),
                "write_latency_gt_16ms": get_stat("write_latency_gt_16ms"),
                "write_latency_gt_32ms": get_stat("write_latency_gt_32ms"),
                "write_latency_gt_64ms": get_stat("write_latency_gt_64ms"),
                "write_latency_gt_128ms": get_stat("write_latency_gt_128ms"),
                "total_read_ops": get_stat("read_success") + get_stat("read_error") + get_stat("read_not_found"),
                "total_write_ops": get_stat("write_success") + get_stat("write_error"),
            })
        
        client.close()
        return {"success": True, "data": latency_data}
    except Exception as e:
        return {"success": False, "error": str(e)}