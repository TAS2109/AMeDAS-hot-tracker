// compare.js - 比較ページ（2〜4地点・全国約1,300地点から検索して追加）

const initEl = document.getElementById("compare-init");
let selectedIds = (initEl.dataset.ids || "").split(",").map(s => s.trim()).filter(Boolean).slice(0, 4);
let labelsById = {}; // id -> "都道府県 地点名"（検索結果 or 比較結果から補完）

const searchInput = document.getElementById("compare-search");
const suggestionsEl = document.getElementById("compare-suggestions");
const chipsWrap = document.getElementById("compare-chips");
let compareChart = null;
let debounceTimer = null;

function syncUrl() {
  const url = new URL(window.location);
  url.searchParams.set("ids", selectedIds.join(","));
  window.history.replaceState({}, "", url);
}

function renderChips() {
  chipsWrap.innerHTML = "";
  selectedIds.forEach((id) => {
    const label = labelsById[id] || id;
    const chip = document.createElement("span");
    chip.className = "compare-chip";
    chip.innerHTML = `${label} <button type="button" data-id="${id}">×</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      selectedIds = selectedIds.filter(x => x !== id);
      renderChips();
      syncUrl();
      loadCompare();
    });
    chipsWrap.appendChild(chip);
  });
}

function addStation(id, label) {
  if (selectedIds.length >= 4) {
    alert("比較できるのは最大4地点までです");
    return;
  }
  if (selectedIds.includes(id)) return;
  labelsById[id] = label;
  selectedIds.push(id);
  renderChips();
  syncUrl();
  loadCompare();
}

function hideSuggestions() {
  suggestionsEl.innerHTML = "";
  suggestionsEl.classList.remove("open");
}

async function runSuggestSearch() {
  const q = searchInput.value.trim();
  if (!q) { hideSuggestions(); return; }
  try {
    const items = await fetchJSON(`/api/search?q=${encodeURIComponent(q)}&limit=15`);
    suggestionsEl.innerHTML = "";
    if (!items.length) {
      suggestionsEl.innerHTML = '<li class="loading">該当する地点がありません</li>';
      suggestionsEl.classList.add("open");
      return;
    }
    items.forEach((s) => {
      const li = document.createElement("li");
      const label = `${s.pref} ${s.name}`;
      li.textContent = label;
      if (selectedIds.includes(s.id)) li.classList.add("disabled");
      li.addEventListener("click", () => {
        if (selectedIds.includes(s.id)) return;
        addStation(s.id, label);
        searchInput.value = "";
        hideSuggestions();
      });
      suggestionsEl.appendChild(li);
    });
    suggestionsEl.classList.add("open");
  } catch (e) { /* noop */ }
}

searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSuggestSearch, 200);
});
searchInput.addEventListener("focus", runSuggestSearch);
document.addEventListener("click", (e) => {
  if (!e.target.closest(".compare-picker")) hideSuggestions();
});

function toLabelTime(iso) {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const PALETTE = ["#d1402f", "#3b82c4", "#2e9e5b", "#e08424"];

function rankText(item) {
  if (!item.rank_national) return "-";
  return `全国${item.rank_national}位 / ${item.station.pref}${item.rank_pref}位`;
}

function renderTable(items) {
  const card = document.getElementById("compare-table-card");
  const table = document.getElementById("compare-table");
  if (!items.length) { card.style.display = "none"; return; }
  card.style.display = "";

  const rows = [
    ["地点", i => `${i.station.pref} ${i.station.name}`],
    ["現在気温", i => `${i.current_temp.toFixed(1)}℃`],
    ["今日の最高", i => `${i.today_max.toFixed(1)}℃`],
    ["推定最高", i => `${i.estimated_max.toFixed(1)}℃`],
    ["昇温速度", i => `${i.pace_per_hour >= 0 ? "+" : ""}${i.pace_per_hour.toFixed(2)}℃/時`],
    ["湿度", i => (i.humidity !== null && i.humidity !== undefined) ? `${i.humidity}%` : "-"],
    ["風速", i => (i.wind_speed !== null && i.wind_speed !== undefined) ? `${i.wind_speed}m/s` : "-"],
    ["40℃到達確率", i => `${i.prob_40.toFixed(1)}%`],
    ["全国/都道府県順位", rankText],
  ];

  let html = "<tr><th></th>" + items.map(i => `<th>${i.station.name}</th>`).join("") + "</tr>";
  rows.forEach(([label, fn]) => {
    html += `<tr><th>${label}</th>` + items.map(i => `<td>${fn(i)}</td>`).join("") + "</tr>";
  });
  table.innerHTML = html;
}

function renderGraph(items) {
  const card = document.getElementById("compare-graph-card");
  if (!items.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const ctx = document.getElementById("compare-chart").getContext("2d");
  const datasets = items.map((item, idx) => ({
    label: `${item.station.name}`,
    data: item.series.map(p => ({ x: toLabelTime(p.time), y: p.temp })),
    borderColor: PALETTE[idx % PALETTE.length],
    pointRadius: 0,
    borderWidth: 2,
    tension: 0.25,
  }));
  if (compareChart) compareChart.destroy();
  compareChart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      animation: false,
      scales: {
        x: { type: "category", ticks: { maxTicksLimit: 8, font: { size: 10 } } },
        y: { ticks: { font: { size: 10 } }, title: { display: true, text: "℃", font: { size: 10 } } },
      },
      plugins: { legend: { labels: { font: { size: 11 } } } },
    },
  });
}

async function loadCompare() {
  if (!selectedIds.length) {
    document.getElementById("compare-table-card").style.display = "none";
    document.getElementById("compare-graph-card").style.display = "none";
    return;
  }
  try {
    const items = await fetchJSON(`/api/compare?ids=${selectedIds.join(",")}`);
    // ラベルを比較結果から補完（URL直打ちで初期表示した場合など）
    items.forEach((i) => { labelsById[i.station.id] = `${i.station.pref} ${i.station.name}`; });
    renderChips();
    renderTable(items);
    renderGraph(items);
  } catch (e) { console.error(e); }
}

renderChips();
loadCompare();

