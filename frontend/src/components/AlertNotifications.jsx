import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, Check, ChevronDown, Loader } from "lucide-react";
import { alertsApi } from "../api/api";

const MAX_ITEMS = 30;

/**
 * Alert notification center.
 *
 * The backend has no push channel of its own, so alerts arrive over SSE
 * (one-way, which is all a notification feed needs) and are reconciled
 * with GET /alerts on connect. If the stream drops, we fall back to
 * polling so the badge still updates.
 */
export default function AlertNotifications() {
  const [alerts, setAlerts] = useState([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState(false);
  const [streamFailed, setStreamFailed] = useState(false);
  const panelRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const res = await alertsApi.list(MAX_ITEMS);
      if (Array.isArray(res.data?.alerts)) setAlerts(res.data.alerts);
    } catch {
      /* the health badge already surfaces backend reachability */
    }
  }, []);

  // Live SSE feed, with a polling fallback if the stream cannot be opened.
  useEffect(() => {
    let source;
    let pollTimer;
    let closed = false;

    const startPolling = () => {
      if (pollTimer || closed) return;
      pollTimer = setInterval(refresh, 15000);
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    // Seed the list and open the stream from an async callback rather than
    // synchronously in the effect body: EventSource + the initial fetch
    // both update state, and doing that inline cascades renders.
    Promise.resolve().then(() => {
      if (closed) return;
      refresh();

      try {
        source = new EventSource(alertsApi.streamUrl());

        const onAlert = (raw) => {
          try {
            const event = JSON.parse(raw.data);
            const record = event?.data;
            if (!record?.fingerprint) return;
            refresh();
            setUnread((n) => n + 1);
          } catch {
            refresh();
          }
        };

        source.addEventListener("alert.firing", onAlert);
        source.addEventListener("alert.resolved", onAlert);
        source.addEventListener("alert.investigated", onAlert);

        source.onopen = () => {
          setLive(true);
          setStreamFailed(false);
          stopPolling();
        };

        source.onerror = () => {
          setLive(false);
          setStreamFailed(true);
          // EventSource retries on its own, but poll too so the UI is not
          // frozen if the backend is fully gone.
          startPolling();
        };
      } catch {
        setStreamFailed(true);
        startPolling();
      }
    });

    return () => {
      closed = true;
      stopPolling();
      if (source) source.close();
    };
  }, [refresh]);

  // Close the panel on outside click / Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    setOpen((v) => !v);
    setUnread(0);
  };

  return (
    <div className="alertbell" ref={panelRef}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label={unread ? `Alerts, ${unread} unread` : "Alerts"}
        className="alertbell__trigger"
      >
        <Bell size={16} />
        {unread > 0 && <span className="alertbell__badge">{unread > 9 ? "9+" : unread}</span>}
        <ChevronDown size={14} className={open ? "alertbell__caret alertbell__caret--open" : "alertbell__caret"} />
      </button>

      {open && (
        <div className="alertbell__panel" role="dialog" aria-label="Alerts">
          <header className="alertbell__head">
            <span>Alerts</span>
            <span className={`alertbell__live ${live ? "alertbell__live--on" : ""}`}>
              {live ? "live" : streamFailed ? "polling" : "connecting"}
            </span>
          </header>

          {alerts.length === 0 ? (
            <p className="alertbell__empty">No alerts recorded yet.</p>
          ) : (
            <ul className="alertbell__list">
              {alerts.map((a) => (
                <li key={a.fingerprint} className="alertbell__item">
                  <span
                    className={`alertbell__dot alertbell__dot--${a.status === "firing" ? "firing" : "ok"}`}
                  />
                  <div className="alertbell__body">
                    <div className="alertbell__title">
                      {a.alertname}
                      {a.severity && <em className="alertbell__sev">{a.severity}</em>}
                    </div>
                    <div className="alertbell__meta">
                      {a.pod || a.namespace || "-"}
                      {a.notification_count > 1 && ` · ${a.notification_count} notifications`}
                      {a.investigation_status && ` · RCA ${a.investigation_status}`}
                    </div>
                  </div>
                  <span
                    className={`alertbell__state alertbell__state--${a.status === "firing" ? "firing" : "ok"}`}
                  >
                    {a.status === "firing" ? <Loader size={11} /> : <Check size={11} />}
                    {a.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
