import { useEffect, useState } from "react";
import api from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import {
  ExternalLink,
  BookOpen,
  Database,
  ArrowUpRight,
  RefreshCw,
} from "lucide-react";

const LINKS = [
  {
    href: "http://localhost:3000",
    title: "Grafana",
    sub: "Dashboards & alerting",
    icon: BookOpen,
  },
  {
    href: "http://127.0.0.1:8001/docs",
    title: "Swagger API",
    sub: "Backend documentation",
    icon: BookOpen,
  },
  {
    href: "http://localhost:8428",
    title: "VictoriaMetrics",
    sub: "Time-series query UI",
    icon: Database,
  },
  {
    href: "http://127.0.0.1:8001",
    title: "Backend root",
    sub: "FastAPI service",
    icon: ArrowUpRight,
  },
];

export default function Settings() {
  const [status, setStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [rechecking, setRechecking] = useState(false);

  async function load() {
    const checks = {
      backend: {
        label: "Backend API",
        endpoint: "http://127.0.0.1:8001/api",
        check: async () => {
          await api.get("/health");
          return "Operating";
        },
      },
      kubernetes: {
        label: "Kubernetes Cluster",
        endpoint: "kind-opensre-demo",
        check: async () => {
          await api.get("/kubernetes/nodes");
          return "Connected";
        },
      },
      metrics: {
        label: "VictoriaMetrics",
        endpoint: "http://localhost:8428",
        check: async () => {
          await api.get("/metrics/health");
          return "Connected";
        },
      },
      github: {
        label: "GitHub Integration",
        endpoint: "api.github.com",
        check: async () => {
          const res = await api.get("/github/health");
          return res.data.success ? "Connected" : "Unreachable";
        },
      },
      opensre: {
        label: "OpenSRE CLI",
        endpoint: "AI investigation engine",
        check: async () => {
          const res = await api.get("/opensre/version");
          if (!res.data.success) return "Not installed";
          return "Installed";
        },
      },
    };

    const entries = await Promise.all(
      Object.entries(checks).map(async ([key, config]) => {
        try {
          return [key, { label: config.label, endpoint: config.endpoint, value: await config.check() }];
        } catch {
          return [
            key,
            {
              label: config.label,
              endpoint: config.endpoint,
              value: key === "backend" ? "Offline" : "Disconnected",
            },
          ];
        }
      })
    );

    return Object.fromEntries(entries);
  }

  useEffect(() => {
    let cancelled = false;

    load().then(
      (result) => {
        if (cancelled) return;
        setStatus(result);
        setLoading(false);
      },
      (err) => {
        console.error("Health check failed:", err);
        if (!cancelled) setLoading(false);
      }
    );

    return () => {
      cancelled = true;
    };
  }, []);

  const entries = Object.values(status);

  function isOk(value) {
    return (
      value &&
      !value.toLowerCase().startsWith("dis") &&
      value !== "Offline" &&
      value !== "NotFound"
    );
  }

  function badgeTone(value) {
    if (value === "Not installed") return "neutral";
    return isOk(value) ? "success" : "danger";
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="page-head__sub">
            Connectivity of the platform services.
          </p>
        </div>

        <div className="page-head__actions">
          <button
            type="button"
            className="btn btn--ghost"
            disabled={rechecking}
            onClick={() => {
              setRechecking(true);
              load()
                .then(setStatus)
                .catch((err) =>
                  console.error("Health check failed:", err)
                )
                .finally(() => setRechecking(false));
            }}
          >
            <RefreshCw
              size={14}
              className={rechecking ? "btn__spinner" : ""}
            />
            Re-check
          </button>
        </div>
      </div>

      <div className="section-label">Service status</div>

      <Card>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Service</th>
                <th>Endpoint</th>
                <th className="cell-end">Status</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? [0, 1, 2, 3].map((i) => (
                    <tr key={i}>
                      <td>
                        <div className="skeleton" style={{ width: 120, height: 14 }} />
                      </td>
                      <td>
                        <div className="skeleton" style={{ width: 160, height: 14 }} />
                      </td>
                      <td className="cell-end">
                        <div
                          className="skeleton"
                          style={{ width: 80, height: 20, marginLeft: "auto" }}
                        />
                      </td>
                    </tr>
                  ))
                : entries.map((item) => (
                    <tr key={item.label}>
                      <td className="cell-strong">{item.label}</td>
                      <td className="cell-mono cell-muted">{item.endpoint}</td>
                      <td className="cell-end">
                        <Badge tone={badgeTone(item.value)}>
                          {item.value}
                        </Badge>
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="section-label">Endpoints & documentation</div>

      <div className="link-grid">
        {LINKS.map((link) => (
          <a
            key={link.title}
            href={link.href}
            target="_blank"
            rel="noreferrer"
            className="link-card"
          >
            <div className="link-card__icon">
              <link.icon size={18} strokeWidth={1.8} />
            </div>

            <div>
              <div className="link-card__title">{link.title}</div>
              <div className="link-card__sub">{link.sub}</div>
            </div>

            <ExternalLink size={15} className="link-card__arrow" />
          </a>
        ))}
      </div>

      <div className="section-label">Configuration</div>

      <Card>
        <div className="table-wrap">
          <table className="table">
            <tbody>
              <tr>
                <td className="cell-strong">Backend API URL</td>
                <td className="cell-mono cell-muted">
                  http://127.0.0.1:8001/api
                </td>
              </tr>
              <tr>
                <td className="cell-strong">VictoriaMetrics</td>
                <td className="cell-mono cell-muted">
                  http://localhost:8428
                </td>
              </tr>
              <tr>
                <td className="cell-strong">Grafana</td>
                <td className="cell-mono cell-muted">
                  http://localhost:3000
                </td>
              </tr>
              <tr>
                <td className="cell-strong">Active cluster context</td>
                <td className="cell-mono cell-muted">kind-opensre-demo</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}