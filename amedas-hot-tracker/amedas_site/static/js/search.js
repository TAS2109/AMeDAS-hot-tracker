// search.js - 地点検索ページ（全国約1,300地点対応・サーバー側検索で高速化）

const input = document.getElementById("search-input");
const listEl = document.getElementById("search-result-list");
const prefRow = document.getElementById("pref-chip-row");

let activePref = null;
let debounceTimer = null;
let requestSeq = 0;

async function renderPrefChips() {
  try {
    const prefs = await fetchJSON("/api/prefs");
    prefRow.innerHTML = "";
    prefs.forEach((pref) => {
      const chip = document.createElement("span");
      chip.className = "pref-chip" + (activePref === pref ? " active" : "");
      chip.textContent = pref;
      chip.addEventListener("click", () => {
        activePref = activePref === pref ? null : pref;
        renderPrefChips();
        runSearch();
      });
      prefRow.appendChild(chip);
    });
  } catch (e) { /* noop */ }
}

function renderResults(items) {
  listEl.innerHTML = "";
  if (!items.length) {
    listEl.innerHTML = '<li class="loading">該当する地点がありません</li>';
    return;
  }
  items.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${s.name}</span><span class="rank-pref">${s.pref}</span>`;
    li.style.cursor = "pointer";
    li.addEventListener("click", () => { window.location.href = `/station/${s.id}`; });
    listEl.appendChild(li);
  });
}

async function runSearch() {
  const q = input.value.trim();
  const seq = ++requestSeq;
  listEl.innerHTML = '<li class="loading skeleton-line">検索中...</li>';
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (activePref) params.set("pref", activePref);
    params.set("limit", "60");
    const items = await fetchJSON(`/api/search?${params.toString()}`);
    if (seq !== requestSeq) return; // 古いレスポンスは無視（連打対策）
    renderResults(items);
  } catch (e) {
    if (seq === requestSeq) listEl.innerHTML = '<li class="loading">検索に失敗しました</li>';
  }
}

input.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSearch, 200); // デバウンスで検索高速化・サーバー負荷軽減
});

renderPrefChips();
runSearch();

