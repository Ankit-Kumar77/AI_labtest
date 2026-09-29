"""Alertmanager observability API for the dashboard.

Read-only views of Alertmanager's own state plus the two demo controls
(sync now, inject a test alert), so the Alerting page can show that the
whole alerting chain is genuinely working rather than assert it.
"""

from fastapi import APIRouter, Query

from app.services import alert_store
from app.services import alertmanager as am

router = APIRouter(
    prefix="/api/alertmanager",
    tags=["Alertmanager"],
)


@router.get("/overview")
def overview():
    """Health, routing config, alerts and silences in one round trip."""
    return am.overview()


@router.get("/health")
def health():
    return am.health()


@router.get("/status")
def status():
    return am.status()


@router.get("/alerts")
def alerts():
    return am.list_alerts()


@router.get("/silences")
def silences():
    return am.silences()


@router.get("/logs")
def logs(tail: int = Query(default=150, ge=1, le=2000)):
    """Live Alertmanager container log tail (alerts arriving continuously)."""
    return am.logs(tail=tail)


@router.get("/reports")
def reports(limit: int = Query(default=50, ge=1, le=200)):
    """Saved RCA reports for alert-driven investigations, incl. failures."""
    return am.reports(limit=limit)


@router.post("/sync")
def sync():
    """Pull current alert state into the incident store now."""
    result = am.sync()
    result["incidents"] = alert_store.active_alerts()
    return result


@router.post("/test-alert")
def test_alert(name: str = "OpenSREDemoAlert", severity: str = "critical"):
    """Inject a synthetic alert to demonstrate the end-to-end chain."""
    return am.send_test_alert(name=name, severity=severity)
