// index.js - トップページ（マップ・ランキング）

const MAP_LAT_RANGE = [24.0, 45.6];
const MAP_LON_RANGE = [122.8, 145.9];
const MAP_W = 500, MAP_H = 620;

function project(lat, lon) {
  const x = ((lon - MAP_LON_RANGE[0]) / (MAP_LON_RANGE[1] - MAP_LON_RANGE[0])) * MAP_W;
  const y = MAP_H - ((lat - MAP_LAT_RANGE[0]) / (MAP_LAT_RANGE[1] - MAP_LAT_RANGE[0])) * MAP_H;
  return [x, y];
}

let currentRankingType = "current";
let ALL_STATIONS = [];

function renderMap(stations) {
  const svg = document.getElementById("japan-map");
  if (!svg) return;
  svg.innerHTML = "";
  const ns = "http://www.w3.org/2000/svg";

  stations.forEach((s) => {
    const [x, y] = project(s.lat, s.lon);
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("cx", x);
    circle.setAttribute("cy", y);
    circle.setAttribute("r", 5.2);
    circle.setAttribute("fill", COLOR_MAP[s.color] || COLOR_MAP.unknown);
    circle.setAttribute("class", "map-point");
    circle.addEventListener("click", () => {
      window.location.href = `/station/${s.id}`;
    });
    const title = document.createElementNS(ns, "title");
    title.textContent = `${s.name}（${s.pref}） 現在${s.current_temp}℃ / 推定最高${s.estimated_max}℃`;
    circle.appendChild(title);
    svg.appendChild(circle);
  });
}

function updateHero(stations) {
  if (!stations.length) return;
  const sorted = [...stations].sort((a, b) => b.current_temp - a.current_temp);
  const top = sorted[0];
  document.getElementById("hero-temp").textContent = `${top.current_temp.toFixed(1)}℃`;
  document.getElementById("hero-place").textContent = `${top.pref} ${top.name}`;
  document.getElementById("hero-count40").textContent = stations.filter(s => s.estimated_max >= 40).length;
  document.getElementById("hero-count38").textContent = stations.filter(s => s.current_temp >= 38).length;
  document.getElementById("hero-count35").textContent = stations.filter(s => s.current_temp >= 35).length;
}

function rankingUnit(type) {
  if (type === "prob40") return "%";
  if (type === "pace") return "℃/時";
  return "℃";
}

function rankingValue(item, type) {
  const keyMap = { current: "current_temp", today_max: "today_max", estimated_max: "estimated_max", prob40: "prob_40", pace: "pace_per_hour" };
  const v = item[keyMap[type]];
  return typeof v === "number" ? v.toFixed(1) : "-";
}

async function loadRanking(type) {
  const list = document.getElementById("ranking-list");
  list.innerHTML = '<li class="loading skeleton-line">読み込み中...</li>';
  try {
    const data = await fetchJSON(`/api/ranking?type=${type}&limit=20`);
    list.innerHTML = "";
    data.items.forEach((item, idx) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="rank-num ${idx === 0 ? "top1" : idx < 3 ? "top3" : ""}">${idx + 1}</span>
        <span class="color-dot" style="background:${COLOR_MAP[item.color] || COLOR_MAP.unknown}"></span>
        <span class="rank-name">${item.name}<br><span class="rank-pref">${item.pref}</span></span>
        <span class="rank-value">${rankingValue(item, type)}${rankingUnit(type)}</span>
      `;
      li.addEventListener("click", () => { window.location.href = `/station/${item.id}`; });
      li.style.cursor = "pointer";
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = '<li class="loading">読み込みに失敗しました</li>';
  }
}

function setupTabs() {
  const tabs = document.querySelectorAll("#ranking-tabs .tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentRankingType = tab.dataset.type;
      loadRanking(currentRankingType);
    });
  });
}

async function init() {
  setupTabs();
  loadRanking(currentRankingType);
  try {
    ALL_STATIONS = await fetchJSON("/api/stations");
    updateHero(ALL_STATIONS);
    renderMap(ALL_STATIONS);
  } catch (e) {
    console.error(e);
  }
}

init();
setInterval(async () => {
  try {
    ALL_STATIONS = await fetchJSON("/api/stations");
    updateHero(ALL_STATIONS);
    renderMap(ALL_STATIONS);
    loadRanking(currentRankingType);
  } catch (e) { /* noop */ }
}, 90000);
