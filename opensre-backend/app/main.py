from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.health import router as health_router
from app.routes.kubernetes import router as kubernetes_router
from app.routes.metrics import router as metrics_router
from app.routes.opensre import router as opensre_router
from app.routes.investigation import router as investigation_router
from app.routes.aerospike import router as aerospike_router
from app.routes.yugabyte import router as yugabyte_router
from app.routes.github import router as github_router
from app.routes.chaos import router as chaos_router
from app.routes.demo import router as demo_router
from app.routes.db_investigation import router as db_investigation_router
from app.routes.nginx import router as nginx_router
from app.routes.nginx_demo import router as nginx_demo_router
from app.routes.coredns import router as coredns_router
from app.routes.coredns_demo import router as coredns_demo_router
from app.routes.elasticsearch import router as elasticsearch_router
from app.routes.elk_demo import router as elk_demo_router
from app.routes.incidents import router as incidents_router

app = FastAPI(
    title="OpenSRE Backend",
    description="Backend API for the OpenSRE Demo Platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(kubernetes_router)
app.include_router(metrics_router)
app.include_router(opensre_router)
app.include_router(investigation_router)
app.include_router(aerospike_router)
app.include_router(yugabyte_router)
app.include_router(github_router)
app.include_router(chaos_router)
app.include_router(demo_router)
app.include_router(db_investigation_router)
app.include_router(nginx_router)
app.include_router(nginx_demo_router)
app.include_router(coredns_router)
app.include_router(coredns_demo_router)
app.include_router(elasticsearch_router)
app.include_router(elk_demo_router)
app.include_router(incidents_router)


@app.get("/")
def root():
    return {
        "application": "OpenSRE Backend",
        "status": "running",
        "version": "1.0.0",
    }