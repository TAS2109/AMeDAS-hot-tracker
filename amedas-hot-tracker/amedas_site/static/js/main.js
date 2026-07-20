// main.js - 全ページ共通処理

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`fetch failed: ${url}`);
  return res.json();
}

function formatTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function formatDateTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const mo = d.getMonth() + 1;
  const da = d.getDate();
  return `${mo}/${da} ${formatTime(iso)}`;
}

const COLOR_MAP = {
  blue: "#3b82c4",
  green: "#2e9e5b",
  yellow: "#d6a916",
  orange: "#e08424",
  red: "#d1402f",
  purple: "#8b2fc9",
  unknown: "#9ca3af",
};

async function refreshFooter() {
  const el = document.getElementById("footer-updated");
  if (!el) return;
  try {
    const meta = await fetchJSON("/api/meta");
    el.textContent = `最終更新: ${formatDateTime(meta.generated_at)}` + (meta.mock_mode ? "（デモデータ表示中）" : "");
  } catch (e) {
    el.textContent = "更新時刻を取得できませんでした";
  }
}

refreshFooter();
setInterval(refreshFooter, 60000);
