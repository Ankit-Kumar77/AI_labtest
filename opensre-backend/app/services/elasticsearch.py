"""Elasticsearch evidence connector — read-only, bounded, production-safe.

Provides controlled queries against the ELK stack so OpenSRE can
investigate incidents using application and Kubernetes logs. All
operations are read-only (no write/delete/index). Results are bounded
by time-range, service/namespace/pod filters, log-level filters and
result limits.

When Elasticsearch is unreachable the connector returns an explicit
"unavailable" marker instead of fabricating data.
"""

import datetime
import logging
import time
from typing import Any

from app.core.config import settings
from app.utils.command import run_command

logger = logging.getLogger("elk")

# ES URL: use internal cluster DNS when running in K8s, localhost for dev
ELASTICSEARCH_URL = getattr(settings, "ELASTICSEARCH_URL", "http://localhost:9200")
ELASTICSEARCH_TIMEOUT = getattr(settings, "ELASTICSEARCH_TIMEOUT", 10)
# Index pattern matches Fluent Bit output: logs-opensre-YYYY.MM.DD
ELASTICSEARCH_INDEX = getattr(settings, "ELASTICSEARCH_INDEX_PATTERN", "logs-opensre-*")

# Patterns OpenSRE cares about - mapped to Fluent Bit field names
# Fluent Bit adds k8s_ prefix to kubernetes metadata fields
PATTERNS = {
    "ERROR": {"regex": r"\bERROR\b", "severity": "error", "field": "log"},
    "EXCEPTION": {"regex": r"(?i)exception|traceback|trace", "severity": "error", "field": "log"},
    "FAILED": {"regex": r"(?i)failed|failure", "severity": "error", "field": "log"},
    "CONNECTION REFUSED": {"regex": r"(?i)connection refused|connect\(\) failed", "severity": "error", "field": "log"},
    "TIMEOUT": {"regex": r"(?i)timeout|timed out|deadline exceeded", "severity": "warning", "field": "log"},
}


class ElasticsearchConnector:
    """Read-only Elasticsearch client wrapper."""

    def __init__(self, url: str | None = None, timeout: int | None = None):
        self.url = url or ELASTICSEARCH_URL
        self.timeout = timeout or ELASTICSEARCH_TIMEOUT
        self._client_instance = None

    def _get_client(self):
        """Lazily create (and cache) the Elasticsearch client."""
        if self._client_instance is None:
            try:
                from elasticsearch import Elasticsearch
                self._client_instance = Elasticsearch(
                    [self.url],
                    request_timeout=self.timeout,
                )
            except Exception as exc:
                logger.error("elasticsearch connect failed: %s", exc)
                return None
        return self._client_instance

    def health(self) -> dict:
        """Check Elasticsearch connectivity (read-only)."""
        try:
            client = self._get_client()
            if client is None:
                return {"success": False, "available": False}
            info = client.info()
            return {"success": True, "available": True, "version": info.get("version", {}).get("number"), "cluster": info.get("cluster_name")}
        except Exception as exc:
            return {"success": False, "available": False, "error": str(exc)}

    def _build_body(self, filters: dict, limit: int = 50, from_offset: int = 0, sort: str = "@timestamp:desc"):
        must = []
        if filters.get("start_time"):
            must.append({"range": {"@timestamp": {"gte": filters["start_time"]}}})
        if filters.get("end_time"):
            must.append({"range": {"@timestamp": {"lte": filters["end_time"]}}})
        # Fluent Bit uses k8s_ prefix for kubernetes metadata
        if filters.get("service"):
            must.append({"term": {"k8s_labels.app.keyword": filters["service"]}})
        if filters.get("namespace"):
            must.append({"term": {"k8s_namespace_name": filters["namespace"]}})
        if filters.get("pod"):
            must.append({"term": {"k8s_pod_name": filters["pod"]}})
        if filters.get("container"):
            must.append({"term": {"k8s_container_name": filters["container"]}})
        if filters.get("level"):
            must.append({"term": {"level": filters["level"]}})
        if filters.get("pattern"):
            must.append({"query_string": {"query": filters["pattern"], "default_field": "log"}})
        if not must:
            must.append({"match_all": {}})
        body = {
            "from": from_offset,
            "size": max(1, min(limit, 100)),
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"must": must}},
        }
        return body

    def search_logs(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        namespace: str | None = None,
        pod: str | None = None,
        container: str | None = None,
        level: str | None = None,
        pattern: str | None = None,
        limit: int = 50,
        from_offset: int = 0,
    ) -> dict:
        """Search logs with bounded, read-only filters."""
        try:
            client = self._get_client()
            if client is None:
                return {"success": False, "available": False}
            body = self._build_body({
                "start_time": start_time, "end_time": end_time,
                "service": service, "namespace": namespace,
                "pod": pod, "container": container,
                "level": level, "pattern": pattern,
            }, limit=limit, from_offset=from_offset)
            result = client.search(index=ELASTICSEARCH_INDEX, body=body)
            hits = result.get("hits", {}).get("hits", [])
            total = result.get("hits", {}).get("total", {})
            total_val = total.get("value", len(hits)) if isinstance(total, dict) else total
            return {
                "success": True, "available": True,
                "total": total_val,
                "limit": limit,
                "hits": [
                    {"_id": h.get("_id"), "_source": h.get("_source", {}), "@timestamp": h.get("_source", {}).get("@timestamp")}
                    for h in hits
                ],
            }
        except Exception as exc:
            logger.error("search_logs failed: %s", exc)
            return {"success": False, "available": False, "error": str(exc)}

    def get_recent_errors(
        self,
        since_minutes: int = 60,
        service: str | None = None,
        namespace: str | None = None,
        limit: int = 30,
    ) -> dict:
        """Return recent ERROR-level log lines (bounded)."""
        end = datetime.datetime.utcnow().isoformat() + "Z"
        start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
        return self.search_logs(
            start_time=start, end_time=end,
            service=service, namespace=namespace,
            level="ERROR", pattern=None, limit=limit,
        )

    def get_service_logs(
        self,
        service: str,
        since_minutes: int = 60,
        limit: int = 50,
    ) -> dict:
        """Return logs for a specific service."""
        end = datetime.datetime.utcnow().isoformat() + "Z"
        start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
        return self.search_logs(
            start_time=start, end_time=end,
            service=service, limit=limit,
        )

    def get_pod_logs(
        self,
        pod: str,
        namespace: str,
        since_minutes: int = 60,
        limit: int = 50,
    ) -> dict:
        """Return logs for a specific pod."""
        end = datetime.datetime.utcnow().isoformat() + "Z"
        start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
        return self.search_logs(
            start_time=start, end_time=end,
            pod=pod, namespace=namespace, limit=limit,
        )

    def find_error_patterns(
        self,
        namespace: str | None = None,
        since_minutes: int = 60,
        limit: int = 20,
    ) -> dict:
        """Find ERROR/EXCEPTION/FAILED/CONNECTION REFUSED/TIMEOUT lines."""
        end = datetime.datetime.utcnow().isoformat() + "Z"
        start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
        all_hits = []
        for label, cfg in PATTERNS.items():
            res = self.search_logs(
                start_time=start, end_time=end,
                namespace=namespace, pattern=cfg["regex"],
                limit=limit,
            )
            if res.get("success") and res.get("available"):
                for hit in res.get("hits", []):
                    src = hit.get("_source", {})
                    all_hits.append({
                        "pattern": label,
                        "severity": cfg["severity"],
                        "timestamp": hit.get("@timestamp"),
                        "message": src.get("log", src.get("message", "")),
                        "service": src.get("k8s_labels", {}).get("app", src.get("service", "")),
                        "namespace": src.get("k8s_namespace_name", src.get("namespace", "")),
                        "pod": src.get("k8s_pod_name", src.get("pod", "")),
                        "container": src.get("k8s_container_name", src.get("container", "")),
                        "level": src.get("level", ""),
                        "context": src,
                    })
        return {
            "success": True, "available": True,
            "patterns_found": len(all_hits),
            "results": all_hits[:limit],
        }

    def error_summary(
        self,
        namespace: str | None = None,
        since_minutes: int = 60,
    ) -> dict:
        """Compact summary of error counts per pattern."""
        end = datetime.datetime.utcnow().isoformat() + "Z"
        start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
        counts: dict[str, int] = {}
        samples: dict[str, list[str]] = {}
        for label, cfg in PATTERNS.items():
            res = self.search_logs(
                start_time=start, end_time=end,
                namespace=namespace, pattern=cfg["regex"],
                limit=5,
            )
            if res.get("success") and res.get("available"):
                counts[label] = res.get("total", 0)
                samples[label] = [
                    h.get("_source", {}).get("log", h.get("_source", {}).get("message", ""))[:300]
                    for h in res.get("hits", [])[:3]
                ]
        return {"success": True, "available": True, "counts": counts, "samples": samples}

    # NEW: Get facets for UI filters
    def get_facets(
        self,
        namespace: str | None = None,
        since_minutes: int = 60,
        size: int = 20,
    ) -> dict:
        """Get unique values for faceted search (namespaces, pods, services, levels, containers).
        If namespace is provided, pods/services/containers are filtered to that namespace.
        """
        try:
            client = self._get_client()
            if client is None:
                return {"success": False, "available": False}

            # Build filter for time range
            end = datetime.datetime.utcnow().isoformat() + "Z"
            start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"
            time_filter = {"range": {"@timestamp": {"gte": start, "lte": end}}}

            if namespace:
                time_filter = {"bool": {"must": [time_filter, {"term": {"k8s_namespace_name": namespace}}]}}

            # Facet aggregations
            aggs = {
                "namespaces": {"terms": {"field": "k8s_namespace_name.keyword", "size": size}},
                "pods": {"terms": {"field": "k8s_pod_name", "size": size}},
                "services": {"terms": {"field": "k8s_labels.app.keyword", "size": size}},
                "levels": {"terms": {"field": "level", "size": 10}},
                "containers": {"terms": {"field": "k8s_container_name", "size": size}},
            }

            body = {
                "size": 0,
                "query": time_filter,
                "aggs": aggs,
            }

            result = client.search(index=ELASTICSEARCH_INDEX, body=body)
            aggs_result = result.get("aggregations", {})

            def extract_buckets(agg_name):
                return [b["key"] for b in aggs_result.get(agg_name, {}).get("buckets", [])]

            return {
                "success": True, "available": True,
                "namespaces": extract_buckets("namespaces"),
                "pods": extract_buckets("pods"),
                "services": extract_buckets("services"),
                "levels": extract_buckets("levels"),
                "containers": extract_buckets("containers"),
            }
        except Exception as exc:
            logger.error("get_facets failed: %s", exc)
            return {"success": False, "available": False, "error": str(exc)}

    # NEW: Get pods filtered by namespace
    def get_pods_by_namespace(
        self,
        namespace: str,
        since_minutes: int = 60,
        size: int = 100,
    ) -> dict:
        """Get unique pod names filtered by namespace."""
        try:
            client = self._get_client()
            if client is None:
                return {"success": False, "available": False}

            end = datetime.datetime.utcnow().isoformat() + "Z"
            start = (datetime.datetime.utcnow() - datetime.timedelta(minutes=since_minutes)).isoformat() + "Z"

            body = {
                "size": 0,
                "query": {
                    "bool": {
                        "must": [
                            {"range": {"@timestamp": {"gte": start, "lte": end}}},
                            {"term": {"k8s_namespace_name": namespace}},
                        ]
                    }
                },
                "aggs": {
                    "pods": {"terms": {"field": "k8s_pod_name", "size": size}},
                },
            }

            result = client.search(index=ELASTICSEARCH_INDEX, body=body)
            aggs_result = result.get("aggregations", {})
            pods = [b["key"] for b in aggs_result.get("pods", {}).get("buckets", [])]

            return {"success": True, "available": True, "pods": pods}
        except Exception as exc:
            logger.error("get_pods_by_namespace failed: %s", exc)
            return {"success": False, "available": False, "error": str(exc)}


elk_connector = ElasticsearchConnector()


def search_logs(**kwargs) -> dict:
    return elk_connector.search_logs(**kwargs)


def get_recent_errors(**kwargs) -> dict:
    return elk_connector.get_recent_errors(**kwargs)


def get_service_logs(service: str, **kwargs) -> dict:
    return elk_connector.get_service_logs(service, **kwargs)


def get_pod_logs(pod: str, namespace: str, **kwargs) -> dict:
    return elk_connector.get_pod_logs(pod, namespace, **kwargs)


def find_error_patterns(**kwargs) -> dict:
    return elk_connector.find_error_patterns(**kwargs)


def elk_health() -> dict:
    return elk_connector.health()


def error_summary(**kwargs) -> dict:
    return elk_connector.error_summary(**kwargs)


# NEW: Facets endpoint
def get_facets(**kwargs) -> dict:
    return elk_connector.get_facets(**kwargs)


# NEW: Pods by namespace endpoint
def get_pods_by_namespace(**kwargs) -> dict:
    return elk_connector.get_pods_by_namespace(**kwargs)