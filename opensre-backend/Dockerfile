# In-cluster image for the OpenSRE backend.
#
# Alertmanager runs inside the cluster and must reach this service over
# Kubernetes Service DNS, so the backend is also deployed in-cluster
# (see ../infra/k8s/opensre-backend/). This image runs the SAME app code
# as the host backend -- same investigation engine, same incident store,
# same opensre RCA CLI. Only the network targets change (in-cluster DNS
# instead of localhost port-forwards).
FROM python:3.13-slim

# kubectl is required by the EXISTING backend services (containers.py,
# investigation.py, db_investigation.py all shell out to `kubectl`), so the
# image ships it rather than rewriting those call sites.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates procps \
    && curl -sSLo /usr/local/bin/kubectl \
        https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# aerospike 16.0.0 is used here (see requirements.docker.txt) because
# 12.0.0 publishes no CPython 3.13 wheel and does not compile from source.
COPY requirements.docker.txt .
RUN pip install --no-cache-dir -r requirements.docker.txt

# The OpenSRE RCA CLI, the same self-contained binary the host backend
# invokes. RCA stays in the existing engine -- no second implementation.
#
# It is not vendored in git (~350MB). Build with:
#   cp -r ~/.local/bin/.opensre-app <build-context>/
#   podman build -t localhost/opensre-backend:alert .
COPY .opensre-app /opt/opensre
ENV OPENSRE_BINARY=/opt/opensre/opensre
RUN chmod +x /opt/opensre/opensre

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
