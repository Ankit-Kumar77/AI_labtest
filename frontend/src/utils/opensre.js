const ESC = String.fromCharCode(27);
const CSI_RE = /^\[[0-9;]*[a-zA-Z]/;

export function stripAnsi(text = "") {
  return text
    .split(ESC)
    .map((segment, index) => (index === 0 ? segment : segment.replace(CSI_RE, "")))
    .join("")
    .replace(/\r/g, "");
}

export function extractReport(stdout = "") {
  const text = stripAnsi(stdout);

  let lastValid = null;
  const stack = [];
  let inString = false;
  let escaped = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }

    if (ch === '"') {
      inString = true;
    } else if (ch === "{") {
      stack.push(i);
    } else if (ch === "}") {
      const start = stack.pop();
      if (start === undefined) continue;

      let candidate;
      try {
        candidate = JSON.parse(text.slice(start, i + 1));
      } catch {
        continue;
      }

      if (
        candidate &&
        typeof candidate === "object" &&
        !Array.isArray(candidate) &&
        "report" in candidate
      ) {
        lastValid = candidate;
      }
    }
  }

  return lastValid;
}

const ACTION_SECTION_RE =
  /^(?:#{1,4}\s+|\*{1,3})\s*(recommended actions|recommendations|recommended next steps|next steps|recommended remediation|remediation actions|actions)\s*\*{0,3}:?\s*$/i;

const EMPHASIS_RE = /\*\*([^*]+)\*\*|\*([^*]+)\*/g;

function cleanAction(text = "") {
  return text
    .replace(EMPHASIS_RE, "$1$2")
    .replace(/[`_]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function extractRecommendedActions(report = {}) {
  if (
    Array.isArray(report.remediation_steps) &&
    report.remediation_steps.length
  ) {
    return report.remediation_steps;
  }

  if (
    Array.isArray(report.investigation_recommendations) &&
    report.investigation_recommendations.length
  ) {
    return report.investigation_recommendations;
  }

  const lines = stripAnsi(report.report || "").split("\n");
  const actions = [];
  let inSection = false;

  for (const raw of lines) {
    const trimmed = raw.replace(/\s+$/, "").trim();

    if (!trimmed) continue;

    if (ACTION_SECTION_RE.test(trimmed)) {
      inSection = true;
      continue;
    }

    if (!inSection) continue;

    const nextHeading = trimmed.match(/^(?:#{1,4}\s+|\*{1,3})[^*]/);

    if (nextHeading) {
      inSection = false;
      continue;
    }

    const bullet = trimmed.match(/^[\u2022*-]\s+(.+)$/);
    const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/);

    const item = bullet ? bullet[1] : numbered ? numbered[1] : null;

    if (item) {
      const clean = cleanAction(item);
      if (clean) actions.push(clean);
    }
  }

  return actions;
}

export function podTone(status = "") {
  const value = status.toLowerCase();

  if (value === "running" || value === "succeeded" || value === "completed") {
    return "success";
  }

  if (value === "pending" || value === "containercreating" || value === "terminating") {
    return "warning";
  }

  if (
    value.includes("error") ||
    value.includes("crash") ||
    value.includes("failed") ||
    value.includes("evicted") ||
    value.includes("oom") ||
    value.includes("unhealthy")
  ) {
    return "danger";
  }

  return "neutral";
}

export function nodeTone(status = "") {
  return status.toLowerCase() === "ready" ? "success" : "danger";
}

export function readyTone(ready = "") {
  const [readyCount, total] = ready.split("/").map(Number);
  if (Number.isFinite(readyCount) && Number.isFinite(total)) {
    return readyCount >= total && total > 0 ? "success" : "warning";
  }
  return "neutral";
}

export function parseRestarts(value) {
  const match = String(value ?? "").match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

export function severityTone(severity = "") {
  if (severity === "critical") return "danger";
  if (severity === "high") return "danger";
  if (severity === "medium") return "warning";
  return "success";
}

export function deriveSeverity(status = "", restarts = 0) {
  const value = String(status || "").toLowerCase();

  if (
    value.includes("crash") ||
    value.includes("error") ||
    value.includes("failed") ||
    value.includes("evicted") ||
    value.includes("oom") ||
    value.includes("unhealthy") ||
    value.includes("imagepull")
  ) {
    return restarts >= 5 ? "critical" : "high";
  }

  if (
    value === "pending" ||
    value === "containercreating" ||
    value === "terminating" ||
    value.includes("waiting")
  ) {
    return "medium";
  }

  if (
    value === "running" ||
    value === "succeeded" ||
    value === "completed"
  ) {
    return restarts > 3 ? "medium" : "low";
  }

  return "medium";
}
