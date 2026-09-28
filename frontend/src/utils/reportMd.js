export function parseKv(line) {
  const match = line.match(/^\s*([^—:]{1,48}?)(?:—|:)\s*(.*)$/);

  if (!match) return { key: "", value: line.trim() };

  return {
    key: match[1].trim().replace(/^\*+\s*|\s*\*+$/g, ""),
    value: match[2].trim(),
  };
}

export function parseKvLine(line) {
  const parts = line.split("|").map(parseKv).filter(Boolean);

  return parts.length >= 1 ? parts : null;
}

export function isPipeMetaLine(line) {
  return (
    line.split("|").length >= 2 &&
    line.split("|").every((part) => /[—:]/.test(part.trim())) &&
    line.length < 400
  );
}

export function parseFindings(markdown = "") {
  const sections = [];
  let current = null;

  const ensure = (title) => {
    current = { title: String(title).trim(), blocks: [] };
    sections.push(current);
  };

  const push = (block) => {
    if (!current) ensure("");
    current.blocks.push(block);
  };

  const pushItem = (kind, text) => {
    if (!current) ensure("");
    const last = current.blocks[current.blocks.length - 1];

    if (last && last.type === "list" && last.kind === kind) {
      last.items.push(text);
    } else {
      current.blocks.push({ type: "list", kind, items: [text] });
    }
  };

  for (const raw of String(markdown).replace(/\r/g, "").split("\n")) {
    const trimmed = raw.trimEnd().trim();

    if (!trimmed) continue;

    const atx = trimmed.match(/^(#{1,4})\s+(.*)$/);

    if (atx) {
      ensure(atx[2]);
      continue;
    }

    const label = trimmed.match(/^\*{1,3}([^*]+?)\*{1,3}\s*:?\s*$/);

    if (label && trimmed.length < 90) {
      ensure(label[1].replace(/:\s*$/, ""));
      continue;
    }

    if (isPipeMetaLine(trimmed)) {
      push({ type: "kv", items: parseKvLine(trimmed) });
      continue;
    }

    const bullet = trimmed.match(/^[\u2022\u2023*-]\s+(.*)$/);

    if (bullet) {
      const numbered = bullet[1].match(/^\(?\d+[).]\s+(.*)$/);
      pushItem(numbered ? "ordered" : "bullet", numbered ? numbered[1] : bullet[1]);
      continue;
    }

    const ordered = trimmed.match(/^\(?\d+[).]\s+(.*)$/);

    if (ordered) {
      pushItem("ordered", ordered[1]);
      continue;
    }

    push({ type: "paragraph", text: trimmed });
  }

  return sections;
}