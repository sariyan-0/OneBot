function money(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(Number.isFinite(n) ? n : 0);
}

function integer(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Number.isFinite(n) ? n : 0);
}

function bytes(value) {
  let n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return i === 0 ? `${Math.round(n)} ${units[i]}` : `${n.toFixed(2)} ${units[i]}`;
}

function pct(part, total) {
  const p = Number(part || 0);
  const t = Number(total || 0);
  if (!Number.isFinite(p) || !Number.isFinite(t) || t <= 0) return 0;
  return Math.max(0, Math.min(100, (p / t) * 100));
}

function fmtDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

module.exports = { money, integer, bytes, pct, fmtDate };
