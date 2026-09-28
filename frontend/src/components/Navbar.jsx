import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import api from "../api/api";
import { Menu } from "lucide-react";
import AlertNotifications from "./AlertNotifications";

const TITLES = [
  { path: "/", title: "Dashboard", subtitle: "Cluster overview" },
  { path: "/kubernetes", title: "Kubernetes", subtitle: "Nodes & workloads" },
  { path: "/metrics", title: "Metrics", subtitle: "VictoriaMetrics & Grafana" },
  { path: "/latency", title: "Latency", subtitle: "Live request-latency percentiles" },
  { path: "/analysis", title: "AI Analysis", subtitle: "OpenSRE investigations" },
  { path: "/incident", title: "Incident Report", subtitle: "Consolidated incident view" },
  { path: "/settings", title: "Settings", subtitle: "Integration status" },
];

export default function Navbar({ onMenuClick }) {
  const location = useLocation();
  const [healthy, setHealthy] = useState(null);

  const meta =
    TITLES.find((item) => item.path === location.pathname) || TITLES[0];

  useEffect(() => {
    let mounted = true;

    async function check() {
      try {
        const res = await api.get("/health");
        if (mounted) setHealthy(res.data.status === "UP");
      } catch {
        if (mounted) setHealthy(false);
      }
    }

    check();
    const timer = setInterval(check, 15000);

    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <header className="navbar">
      <button
        type="button"
        aria-label="Open navigation"
        className="navbar__menu"
        onClick={onMenuClick}
      >
        <Menu size={18} />
      </button>

      <div className="navbar__crumbs">
        <span className="navbar__title">{meta.title}</span>
        <span className="navbar__divider" />
        <span className="navbar__subtitle">{meta.subtitle}</span>
      </div>

      <div className="navbar__spacer" />

      <AlertNotifications />

      <span
        role="status"
        aria-live="polite"
        className={`navbar__badge ${healthy === false ? "navbar__badge--warn" : "navbar__badge--ok"}`}
      >
        <span className="navbar__badge-dot" />
        {healthy === null
          ? "Checking…"
          : healthy
            ? "Backend healthy"
            : "Backend unreachable"}
      </span>
    </header>
  );
}
