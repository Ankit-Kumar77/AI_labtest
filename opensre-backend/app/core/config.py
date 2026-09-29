import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME = "OpenSRE Backend"
    VERSION = "1.0.0"

    OPENSRE_BINARY = os.getenv("OPENSRE_BINARY", "opensre")

    # LLM provider for the OpenSRE agent.
    #
    # The CLI reads these from the environment, and the backend passes its own
    # environment through to the subprocess, so no CLI-side config file is
    # needed in the pod. Default to openrouter: the Gemini free tier allows only
    # ~20 requests/day, which is not enough for an alert-driven RCA pipeline.
    # Set LLM_PROVIDER=gemini (and GEMINI_API_KEY) to go back to Gemini.
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    # OpenAI-compatible endpoint, used with LLM_PROVIDER=custom-openai to route
    # the CLI through the in-pod token-ceiling shim
    # (app/services/llm_ceiling_proxy.py). The shim holds the real provider key;
    # CUSTOM_OPENAI_API_KEY only has to be non-empty.
    CUSTOM_OPENAI_API_KEY = os.getenv("CUSTOM_OPENAI_API_KEY", "")
    CUSTOM_OPENAI_BASE_URL = os.getenv("CUSTOM_OPENAI_BASE_URL", "")
    CUSTOM_OPENAI_MODEL = os.getenv("CUSTOM_OPENAI_MODEL", "gpt-4o-mini")

    VICTORIA_METRICS_URL = os.getenv(
        "VICTORIA_METRICS_URL",
        "http://localhost:8428",
    )

    GRAFANA_URL = os.getenv(
        "GRAFANA_URL",
        "http://localhost:3000",
    )

    # Alertmanager, reached through a `kubectl port-forward` (see
    # app/services/portforward.py -- FORWARDS["alertmanager"]).
    #
    # Alertmanager's own webhook receiver points at the IN-CLUSTER backend
    # service, but the dashboard is served by the HOST backend, and pods
    # cannot reach the host in this kind/podman setup. So the host backend
    # pulls alert state from Alertmanager's read API instead of receiving
    # the push, and feeds it through the same alert_store lifecycle the
    # webhook uses.
    ALERTMANAGER_URL = os.getenv(
        "ALERTMANAGER_URL",
        "http://localhost:9093",
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