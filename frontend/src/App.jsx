import { Routes, Route } from "react-router-dom";

import Layout from "./components/Layout";

import Dashboard from "./pages/Dashboard";
import Kubernetes from "./pages/Kubernetes";
import Metrics from "./pages/Metrics";
import Latency from "./pages/Latency";
import Aerospike from "./pages/Aerospike";
import Yugabyte from "./pages/Yugabyte";
import AIAnalysis from "./pages/AIAnalysis";
import Incident from "./pages/Incident";
import Settings from "./pages/Settings";
import GitHub from "./pages/GitHub";
import Chaos from "./pages/Chaos";
import Logs from "./pages/Logs";
import Alerting from "./pages/Alerting";

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/kubernetes" element={<Kubernetes />} />
        <Route path="/metrics" element={<Metrics />} />
        <Route path="/latency" element={<Latency />} />
        <Route path="/alerting" element={<Alerting />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/aerospike" element={<Aerospike />} />
        <Route path="/yugabyte" element={<Yugabyte />} />
        <Route path="/analysis" element={<AIAnalysis />} />
        <Route path="/incident" element={<Incident />} />
        <Route path="/github" element={<GitHub />} />
        <Route path="/chaos" element={<Chaos />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  );
}

export default App;