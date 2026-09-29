import logging
from contextlib import asynccontextmanager

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
from app.routes.alerts import router as alerts_router
from app.routes.alertmanager import router as alertmanager_router
from app.services import alert_store

log = logging.getLogger("opensre.alerts")

# How often to sweep for alert state that Alertmanager has stopped
# confirming. Must stay well above `repeat_interval` (1m) so a single slow
# or coalesced webhook is never mistaken for a resolved alert.
REAP_INTERVAL_SECONDS = 60

# How often to pull alert state from Alertmanager's read API. Alertmanager's
# own webhook targets the in-cluster backend, which the host dashboard
# process cannot receive from, so this poll is what actually fills this
# process's alert store. Must stay below Alertmanager's 1m repeat_interval
# so a newly firing alert shows up promptly.
ALERT_SYNC_INTERVAL_SECONDS = 20


def _reap_alert_state() -> None:
    """Reconcile persisted alert state with what Alertmanager still knows.

    A missed `resolved` webhook (we were down, or Alertmanager restarted
    and lost state) leaves an alert pinned to `firing` forever, and a
    process that died mid-investigation leaves a `pending` record owned by
    a thread that no longer exists. Both are repaired here.
    """
    for record in alert_store.recover_orphaned_investigations():
        log.warning(
            "marked orphaned investigation as interrupted: %s/%s",
            record.get("alertname"),
            record.get("pod"),
        )

    for record in alert_store.reap_stale_firing():
        log.warning(
            "auto-resolved stale firing alert (no webhook for %ss): %s/%s",
            alert_store.STALE_FIRING_SECONDS,
            record.get("alertname"),
            record.get("pod"),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.services import alert_events

    # A --reload re-executes this process, so clear any flag a previous
    # shutdown set before opening new SSE streams.
    alert_events.reset_shutdown()

    _reap_alert_state()
    # Keep sweeping: a resolution can be missed at any time, not just on
    # startup, and no later webhook is guaranteed to arrive.
    tasks = [
        asyncio.create_task(_reap_loop()),
        asyncio.create_task(_alert_sync_loop()),
    ]
    try:
        yield
    finally:
        # Release long-lived SSE streams so uvicorn can shut down.
        alert_events.begin_shutdown()
        for task in tasks:
            task.cancel()


async def _reap_loop() -> None:
    import asyncio

    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_reap_alert_state)
        except Exception:  # noqa: BLE001 - the sweeper must never die
            log.exception("alert state reaper failed")


async def _alert_sync_loop() -> None:
    """Keep this process's alert store fed from Alertmanager.

    Syncs once at startup so a freshly started backend immediately shows
    alerts that are already firing, rather than waiting a full interval.
    """
    import asyncio

    from app.services import alertmanager

    while True:
        try:
            await asyncio.to_thread(alertmanager.sync)
        except Exception:  # noqa: BLE001 - the syncer must never die
            log.exception("alertmanager sync failed")
        await asyncio.sleep(ALERT_SYNC_INTERVAL_SECONDS)


app = FastAPI(
    title="OpenSRE Backend",
    description="Backend API for the OpenSRE Demo Platform",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(alerts_router)
app.include_router(alertmanager_router)


@app.get("/")
def root():
    return {
        "application": "OpenSRE Backend",
        "status": "running",
        "version": "1.0.0",
    }