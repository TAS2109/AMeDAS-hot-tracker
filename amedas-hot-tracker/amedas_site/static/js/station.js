// station.js - 地点詳細ページ

const sidEl = document.getElementById("station-sid");
const SID = sidEl.dataset.sid;

let chart = null;
let todaySeries = [], yesterdaySeries = [], normalSeries = [], projectionSeries = [];

function toLabelTime(iso) {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function heatBadgeColor(level) {
  if (level.includes("歴代")) return COLOR_MAP.purple;
  if (level.includes("記録級")) return COLOR_MAP.red;
  if (level.includes("非常に高温")) return COLOR_MAP.orange;
  if (level.includes("真夏日")) return COLOR_MAP.yellow;
  return COLOR_MAP.green;
}

function renderDetail(d) {
  document.getElementById("st-current").textContent = `${d.current_temp.toFixed(1)}℃`;
  document.getElementById("st-estimated").textContent = `${d.prediction.estimated_max.toFixed(1)}℃`;
  document.getElementById("st-range").textContent = `予測幅 ${d.prediction.range_low.toFixed(1)}～${d.prediction.range_high.toFixed(1)}℃`;
  document.getElementById("st-todaymax").textContent = `${d.today_max.toFixed(1)}℃`;
  document.getElementById("st-todaymax-time").textContent = formatDateTime(d.today_max_time);
  document.getElementById("st-prob40").textContent = `${d.prediction.prob_40.toFixed(1)}%`;
  document.getElementById("st-peaktime").textContent = `${d.prediction.peak_time_estimate}頃`;
  document.getElementById("st-probrecord").textContent = d.prediction.prob_record !== null ? `${d.prediction.prob_record.toFixed(1)}%` : "-";

  const badge = document.getElementById("heat-badge");
  badge.textContent = d.heat_level;
  badge.style.background = heatBadgeColor(d.heat_level);

  document.getElementById("an-delta30").textContent = `${d.pace.delta_30m >= 0 ? "+" : ""}${d.pace.delta_30m.toFixed(1)}℃`;
  document.getElementById("an-delta60").textContent = `${d.pace.delta_1h >= 0 ? "+" : ""}${d.pace.delta_1h.toFixed(1)}℃`;
  document.getElementById("an-pace").textContent = `${d.pace.pace_per_hour >= 0 ? "+" : ""}${d.pace.pace_per_hour.toFixed(2)}℃/時`;
  document.getElementById("an-sigma").textContent = `±${d.prediction.sigma.toFixed(1)}℃`;
  document.getElementById("an-similar").textContent = `${d.prediction.similar_day_adjust >= 0 ? "+" : ""}${d.prediction.similar_day_adjust.toFixed(2)}℃`;
  document.getElementById("an-adjust").textContent = `×${d.prediction.weather_adjust_factor.toFixed(2)}`;

  document.getElementById("w-humidity").textContent = (d.humidity !== null && d.humidity !== undefined) ? `${d.humidity}%` : "-";
  document.getElementById("w-wind").textContent = (d.wind_speed !== null && d.wind_speed !== undefined) ? `${d.wind_speed}m/s` : "-";
  document.getElementById("w-winddir").textContent = d.wind_dir || "-";
  document.getElementById("w-sun").textContent = (d.sunshine_min !== null && d.sunshine_min !== undefined) ? `${d.sunshine_min}分/直近1h` : "-";
  document.getElementById("w-pressure").textContent = (d.pressure !== null && d.pressure !== undefined) ? `${d.pressure}hPa` : "-";
  document.getElementById("w-precip").textContent = (d.precip !== null && d.precip !== undefined) ? `${d.precip}mm` : "-";

  document.getElementById("u-t850").textContent = d.upper_air.t850 !== null ? `${d.upper_air.t850}℃` : "-";
  document.getElementById("u-t925").textContent = d.upper_air.t925 !== null ? `${d.upper_air.t925}℃` : "-";
  document.getElementById("u-t975").textContent = d.upper_air.t975 !== null ? `${d.upper_air.t975}℃` : "-";
  document.getElementById("u-analysis").textContent = d.upper_air.analysis;

  document.getElementById("rec-record").textContent = `${d.station.record_high.toFixed(1)}℃`;
  document.getElementById("rec-record-diff").textContent = `あと${d.diff_from_record.toFixed(1)}℃`;
  document.getElementById("rec-year").textContent = `${d.year_high.toFixed(1)}℃`;
  document.getElementById("rec-year-diff").textContent = `差 ${d.diff_from_year_high >= 0 ? "-" : "+"}${Math.abs(d.diff_from_year_high).toFixed(1)}℃`;
  if (d.month_high !== null && d.month_high !== undefined) {
    document.getElementById("rec-month").textContent = `${d.month_high.toFixed(1)}℃`;
    const diffMonth = d.diff_from_month_high;
    document.getElementById("rec-month-diff").textContent = (diffMonth >= 0) ? `あと${diffMonth.toFixed(1)}℃` : `更新中 +${Math.abs(diffMonth).toFixed(1)}℃`;
  } else {
    document.getElementById("rec-month").textContent = "-";
    document.getElementById("rec-month-diff").textContent = "-";
  }
  document.getElementById("rec-estimate-note").style.display = d.station.estimated_values ? "" : "none";
  document.getElementById("rec-normal").textContent = `平年比 ${d.diff_from_normal >= 0 ? "+" : ""}${d.diff_from_normal.toFixed(1)}℃`;
  document.getElementById("rec-rank-nat").textContent = d.rank_national ? `全国 ${d.rank_national}位 / ${d.rank_national_total}地点中` : "-";
  document.getElementById("rec-rank-pref").textContent = d.rank_pref ? `${d.station.pref} ${d.rank_pref}位 / ${d.rank_pref_total}地点中` : "-";

  document.getElementById("add-compare-link").href = `/compare?ids=${SID}`;
}

function buildChart() {
  const ctx = document.getElementById("temp-chart").getContext("2d");
  const showYesterday = document.getElementById("toggle-yesterday").checked;
  const showNormal = document.getElementById("toggle-normal").checked;
  const showProjection = document.getElementById("toggle-projection").checked;

  const datasets = [
    {
      label: "本日",
      data: todaySeries.map(p => ({ x: toLabelTime(p.time), y: p.temp })),
      borderColor: "#d1402f",
      backgroundColor: "rgba(209,64,47,0.08)",
      tension: 0.25,
      pointRadius: 0,
      borderWidth: 2,
    },
  ];
  if (showProjection && projectionSeries.length) {
    datasets.push({
      label: "推定曲線",
      data: projectionSeries.map(p => ({ x: toLabelTime(p.time), y: p.temp })),
      borderColor: "#e08424",
      borderDash: [6, 4],
      pointRadius: 0,
      borderWidth: 2,
    });
  }
  if (showYesterday && yesterdaySeries.length) {
    datasets.push({
      label: "昨日",
      data: yesterdaySeries.map(p => ({ x: toLabelTime(p.time), y: p.temp })),
      borderColor: "#9ca3af",
      pointRadius: 0,
      borderWidth: 1.5,
    });
  }
  if (showNormal && normalSeries.length) {
    datasets.push({
      label: "平年値",
      data: normalSeries.map(p => ({ x: toLabelTime(p.time), y: p.temp })),
      borderColor: "#3b82c4",
      borderDash: [2, 3],
      pointRadius: 0,
      borderWidth: 1.5,
    });
  }

  if (chart) chart.destroy();
  chart = new Chart(ctx, {
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

async function loadSeries() {
  const [today, yesterday, normal] = await Promise.all([
    fetchJSON(`/api/station/${SID}/series?target=today`),
    fetchJSON(`/api/station/${SID}/series?target=yesterday`),
    fetchJSON(`/api/station/${SID}/series?target=normal`),
  ]);
  todaySeries = today.series;
  projectionSeries = today.projection || [];
  yesterdaySeries = yesterday.series;
  normalSeries = normal.series;
  buildChart();
}

async function loadAll() {
  try {
    const detail = await fetchJSON(`/api/station/${SID}`);
    renderDetail(detail);
  } catch (e) { console.error(e); }
  try {
    await loadSeries();
  } catch (e) { console.error(e); }
}

["toggle-yesterday", "toggle-normal", "toggle-projection"].forEach((id) => {
  document.getElementById(id).addEventListener("change", buildChart);
});

loadAll();
setInterval(loadAll, 90000);
