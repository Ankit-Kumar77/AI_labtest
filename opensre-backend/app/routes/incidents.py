from fastapi import APIRouter, Query

from app.services import incident_history

router = APIRouter(
    prefix="/api/incidents",
    tags=["Incidents"],
)


@router.get("")
def list_incidents(limit: int = Query(default=100, ge=1, le=500)):
    """Newest-first persisted investigation history (summaries).

    Every OpenSRE investigation auto-saves here, so analyses run from
    the AI Analysis dashboard (or any other page) stay accessible later
    from the Incident Report page.
    """
    return incident_history.list_incidents(limit=limit)


@router.get("/{incident_id}")
def get_incident(incident_id: str):
    """Full stored record for one investigation (incl. raw CLI output)."""
    return incident_history.get_incident(incident_id)


@router.delete("/{incident_id}")
def delete_incident(incident_id: str):
    """Delete a single stored investigation."""
    return incident_history.delete_incident(incident_id)


@router.delete("")
def clear_incidents():
    """Delete all stored investigations."""
    return incident_history.clear()
