import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Container,
  Activity,
  BrainCircuit,
  FileText,
  Settings,
  Hexagon,
  X,
  Database,
  HardDrive,
  GitBranch,
  Zap,
  Gauge,
  BellRing,
  Database as DatabaseIcon,
} from "lucide-react";

const NAV = [
  {
    section: "Monitor",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
      { to: "/kubernetes", label: "Kubernetes", icon: Container },
      { to: "/metrics", label: "Metrics", icon: Activity },
      { to: "/latency", label: "Latency", icon: Gauge },
      { to: "/alerting", label: "Alerting", icon: BellRing },
      { to: "/logs", label: "Logs", icon: DatabaseIcon },
    ],
  },
  {
    section: "Databases",
    items: [
      { to: "/aerospike", label: "Aerospike", icon: Database },
      { to: "/yugabyte", label: "YugabyteDB", icon: HardDrive },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { to: "/analysis", label: "AI Analysis", icon: BrainCircuit },
      { to: "/incident", label: "Incident Report", icon: FileText },
    ],
  },
  {
    section: "Integrations",
    items: [{ to: "/github", label: "GitHub & CI/CD", icon: GitBranch }],
  },
  {
    section: "Chaos",
    items: [{ to: "/chaos", label: "Failure Injection", icon: Zap }],
  },
  {
    section: "System",
    items: [{ to: "/settings", label: "Settings", icon: Settings }],
  },
];

export default function Sidebar({ open, onClose }) {
  return (
    <aside className={`sidebar${open ? " sidebar--open" : ""}`}>
      <div className="sidebar__brand">
        <div className="sidebar__logo">
          <Hexagon size={22} strokeWidth={2.4} />
        </div>

        <div>
          <div className="sidebar__brand-name">OpenSRE</div>
          <div className="sidebar__brand-sub">Observability</div>
        </div>

        <button
          type="button"
          aria-label="Close navigation"
          className="sidebar__close"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>

      <nav className="sidebar__nav">
        {NAV.map((group) => (
          <div key={group.section}>
            <div className="sidebar__section">{group.section}</div>

            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onClose}
                className={({ isActive }) =>
                  `sidebar__link${isActive ? " sidebar__link--active" : ""}`
                }
              >
                <item.icon size={18} />
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar__footer">
        <div className="sidebar__status">
          <div>
            <div className="sidebar__status-dot" />
          </div>
          <div>
            <div className="sidebar__status-title">All systems nominal</div>
            <div className="sidebar__status-sub">kind-opensre-demo</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
