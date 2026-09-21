import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME = "OpenSRE Backend"
    VERSION = "1.0.0"

    OPENSRE_BINARY = os.getenv("OPENSRE_BINARY", "opensre")

    VICTORIA_METRICS_URL = os.getenv(
        "VICTORIA_METRICS_URL",
        "http://localhost:8428",
    )

    GRAFANA_URL = os.getenv(
        "GRAFANA_URL",
        "http://localhost:3000",
    )

    AEROSPIKE_HOSTS = os.getenv("AEROSPIKE_HOSTS", "aerospike.databases.svc.cluster.local:3000")
    AEROSPIKE_NAMESPACE = os.getenv("AEROSPIKE_NAMESPACE", "test")

    YUGABYTE_HOST = os.getenv("YUGABYTE_HOST", "yugabytedb.databases.svc.cluster.local")
    YUGABYTE_PORT = int(os.getenv("YUGABYTE_PORT", "5433"))
    YUGABYTE_DATABASE = os.getenv("YUGABYTE_DATABASE", "yugabyte")
    YUGABYTE_USER = os.getenv("YUGABYTE_USER", "yugabyte")
    YUGABYTE_PASSWORD = os.getenv("YUGABYTE_PASSWORD", "yugabyte")

    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
    GITHUB_REPO = os.getenv("GITHUB_REPO", "")
    GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

    NGINX_NAMESPACE = os.getenv("NGINX_NAMESPACE", "opensre")
    NGINX_LABEL = os.getenv("NGINX_LABEL", "app=nginx")
    NGINX_CONTAINER_NAME = os.getenv("NGINX_CONTAINER_NAME", "nginx")

    ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    ELASTICSEARCH_TIMEOUT = int(os.getenv("ELASTICSEARCH_TIMEOUT", "10"))
    ELASTICSEARCH_INDEX_PATTERN = os.getenv("ELASTICSEARCH_INDEX_PATTERN", "logs-opensre-*")

    COREDNS_NAMESPACE = os.getenv("COREDNS_NAMESPACE", "kube-system")
    COREDNS_LABEL = os.getenv("COREDNS_LABEL", "k8s-app=kube-dns")
    COREDNS_DEPLOYMENT = os.getenv("COREDNS_DEPLOYMENT", "coredns")
    COREDNS_PROBE_TARGET = os.getenv(
        "COREDNS_PROBE_TARGET",
        "kubernetes.default.svc.cluster.local",
    )


settings = Settings()