# -*- coding: utf-8 -*-
"""
app.py
======
全国アメダス高温予測サイト - Flaskアプリケーション本体。

ルーティング一覧:
  GET  /                         トップページ(ランキング + 全国マップ)
  GET  /station/<sid>            地点詳細ページ
  GET  /compare                  比較ページ（?ids=44132,14163,...）
  GET  /search                   検索ページ

  GET  /api/meta                 最終更新時刻など
  GET  /api/stations             全地点の現況サマリー（マップ・検索用）
  GET  /api/ranking?type=...     ランキング取得
  GET  /api/station/<sid>        地点詳細データ
  GET  /api/station/<sid>/series?target=today|yesterday|normal  時系列
  GET  /api/compare?ids=a,b,c    比較データ一括取得
"""
from __future__ import annotations

import os
import gzip
import time
import threading
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request, abort

import amedas_data as ad
import predictor as pred

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600  # 静的ファイルのブラウザキャッシュ(1時間)で読み込み高速化
JST = ZoneInfo("Asia/Tokyo")

PEAK_HOUR = float(os.environ.get("PEAK_HOUR", "14.5"))
CACHE_TTL_SEC = int(os.environ.get("CACHE_TTL_SEC", "90"))

_cache_lock = threading.Lock()
_cache = {"ts": 0.0, "snapshot": None, "latest_time": None}


_master_lock = threading.Lock()
_master_cache = {"ts": 0.0, "data": None}
MASTER_CACHE_TTL_SEC = int(os.environ.get("MASTER_CACHE_TTL_SEC", "3600"))


def _station_master() -> list[dict]:
    """稼働中の観測点マスタ(全国約1,300地点)を list[dict] で返す。
    id/name/pref/lat/lon/alt/record_high/normal_high/month_high を含む。
    気象庁への取得に失敗した場合は内蔵の主要地点(約59地点)にフォールバックする。
    """
    with _master_lock:
        if _master_cache["data"] is None or (time.time() - _master_cache["ts"]) > MASTER_CACHE_TTL_SEC:
            _master_cache["data"] = ad.get_full_station_master()
            _master_cache["ts"] = time.time()
        return _master_cache["data"]


_search_index_lock = threading.Lock()
_search_index_cache = {"stations": None, "by_pref": None}


def _search_index() -> dict:
    """検索・候補一覧向けの軽量インデックス（都道府県ごとのグルーピングを事前計算）。"""
    with _search_index_lock:
        stations = _station_master()
        if _search_index_cache["stations"] is not stations:
            by_pref: dict[str, list[dict]] = {}
            for s in stations:
                by_pref.setdefault(s["pref"], []).append(s)
            _search_index_cache["stations"] = stations
            _search_index_cache["by_pref"] = by_pref
        return _search_index_cache


def _build_snapshot() -> dict:
    """全地点の現況＋予測をまとめて構築する（キャッシュ対象）。"""
    now = datetime.now(JST)
    stations_out = []

    if ad.USE_MOCK:
        for st in _station_master():
            snap = ad.build_mock_station_snapshot(st, now)
            pace = pred.compute_pace(snap["series"])
            prediction = pred.predict(
                current_temp=snap["current_temp"],
                now=now,
                pace=pace,
                peak_hour=snap["peak_hour"],
                record_high=st["record_high"],
                year_high=max(st["normal_high"] + 5.5, snap["today_max"]),
                month_high=st.get("month_high"),
                humidity=snap.get("humidity"),
                wind_speed=snap.get("wind_speed"),
                sunshine_min=snap.get("sunshine_min"),
            )
            color = pred.temp_color_category(max(snap["current_temp"], prediction["estimated_max"]))
            stations_out.append({
                "id": st["id"], "name": st["name"], "pref": st["pref"],
                "lat": st["lat"], "lon": st["lon"],
                "current_temp": snap["current_temp"],
                "today_max": snap["today_max"],
                "today_max_time": snap["today_max_time"],
                "estimated_max": prediction["estimated_max"],
                "prob_40": prediction["prob_40"],
                "pace_per_hour": pace["pace_per_hour"],
                "color": color,
            })
    else:
        # 実データモード：最新観測マップから現在気温のみ迅速に構築する。
        # 予測(推定最高・40℃確率)は地点別時系列APIが必要になるため、
        # 現在気温が高い上位N地点のみ追加取得して算出する(APIアクセス数を抑制)。
        try:
            latest_time = ad.fetch_latest_time()
            latest_map = ad.fetch_latest_map(latest_time)
        except Exception:
            # ネットワーク不可などの場合はモックへフォールバック
            return _build_snapshot_mock_fallback(now)

        master = {s["id"]: s for s in _station_master()}
        current_list = []
        for st in master.values():
            obs = latest_map.get(st["id"])
            temp = None
            if obs and obs.get("temp") and obs["temp"][0] is not None:
                temp = obs["temp"][0]
            current_list.append((st, temp))

        current_list.sort(key=lambda x: (x[1] is None, -(x[1] or -999)))
        TOP_N = 40
        today = now.date()

        for idx, (st, temp) in enumerate(current_list):
            if temp is None:
                continue
            estimated_max, prob_40, pace_val = temp, 0.0, 0.0
            if idx < TOP_N:
                try:
                    tsdata = ad.fetch_point_timeseries(st["id"], today)
                    series = _parse_point_series(tsdata, today)
                    pace = pred.compute_pace(series)
                    prediction = pred.predict(
                        current_temp=temp, now=now, pace=pace, peak_hour=PEAK_HOUR,
                        record_high=st["record_high"], year_high=None,
                    )
                    estimated_max = prediction["estimated_max"]
                    prob_40 = prediction["prob_40"]
                    pace_val = pace["pace_per_hour"]
                except Exception:
                    pass
            color = pred.temp_color_category(max(temp, estimated_max))
            stations_out.append({
                "id": st["id"], "name": st["name"], "pref": st["pref"],
                "lat": st["lat"], "lon": st["lon"],
                "current_temp": temp, "today_max": temp,
                "today_max_time": now.isoformat(),
                "estimated_max": estimated_max, "prob_40": prob_40,
                "pace_per_hour": pace_val, "color": color,
            })

    return {"generated_at": now.isoformat(), "stations": stations_out}


def _build_snapshot_mock_fallback(now):
    stations_out = []
    for st in _station_master():
        snap = ad.build_mock_station_snapshot(st, now)
        pace = pred.compute_pace(snap["series"])
        prediction = pred.predict(
            current_temp=snap["current_temp"], now=now, pace=pace,
            peak_hour=snap["peak_hour"], record_high=st["record_high"],
            year_high=max(st["normal_high"] + 5.5, snap["today_max"]),
            month_high=st.get("month_high"),
            humidity=snap.get("humidity"), wind_speed=snap.get("wind_speed"),
            sunshine_min=snap.get("sunshine_min"),
        )
        color = pred.temp_color_category(max(snap["current_temp"], prediction["estimated_max"]))
        stations_out.append({
            "id": st["id"], "name": st["name"], "pref": st["pref"],
            "lat": st["lat"], "lon": st["lon"],
            "current_temp": snap["current_temp"], "today_max": snap["today_max"],
            "today_max_time": snap["today_max_time"],
            "estimated_max": prediction["estimated_max"], "prob_40": prediction["prob_40"],
            "pace_per_hour": pace["pace_per_hour"], "color": color,
        })
    return {"generated_at": now.isoformat(), "stations": stations_out}


def _parse_point_series(tsdata: dict, day: date) -> list[dict]:
    out = []
    for key, val in sorted(tsdata.items()):
        temp_field = val.get("temp")
        if not temp_field or temp_field[0] is None:
            continue
        try:
            dt = datetime.strptime(key, "%Y%m%d%H%M%S").replace(tzinfo=JST)
        except ValueError:
            continue
        out.append({"time": dt.isoformat(), "temp": temp_field[0]})
    return out


def get_snapshot() -> dict:
    with _cache_lock:
        if _cache["snapshot"] is None or (time.time() - _cache["ts"]) > CACHE_TTL_SEC:
            _cache["snapshot"] = _build_snapshot()
            _cache["ts"] = time.time()
        return _cache["snapshot"]


def _rank_national_pref(stations: list[dict], sid: str, key: str):
    valid = [s for s in stations if s.get(key) is not None]
    ranked_national = sorted(valid, key=lambda s: -s[key])
    nat_rank = next((i + 1 for i, s in enumerate(ranked_national) if s["id"] == sid), None)

    target = next((s for s in stations if s["id"] == sid), None)
    pref_rank = None
    if target:
        pref_valid = [s for s in valid if s["pref"] == target["pref"]]
        pref_ranked = sorted(pref_valid, key=lambda s: -s[key])
        pref_rank = next((i + 1 for i, s in enumerate(pref_ranked) if s["id"] == sid), None)
    return nat_rank, len(ranked_national), pref_rank, (len(pref_valid) if target else 0)


# ---------------------------------------------------------------------------
# ページルーティング
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", mock=ad.USE_MOCK)


@app.route("/station/<sid>")
def station_page(sid):
    master = {s["id"]: s for s in _station_master()}
    if sid not in master:
        abort(404)
    return render_template("station.html", sid=sid, station=master[sid])


@app.route("/compare")
def compare_page():
    ids = request.args.get("ids", "")
    return render_template("compare.html", ids=ids)


@app.route("/search")
def search_page():
    return render_template("search.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/meta")
def api_meta():
    snap = get_snapshot()
    return jsonify({
        "generated_at": snap["generated_at"],
        "mock_mode": ad.USE_MOCK,
        "station_count": len(snap["stations"]),
        "cache_ttl_sec": CACHE_TTL_SEC,
    })


@app.route("/api/stations")
def api_stations():
    snap = get_snapshot()
    return jsonify(snap["stations"])


@app.route("/api/stations/master")
def api_stations_master():
    """観測点マスタのみを返す軽量エンドポイント（検索・比較ページの候補一覧用）。
    現況値を含まないため get_snapshot() を経由せず高速に応答する。
    """
    pref = request.args.get("pref", "").strip()
    stations = _station_master()
    if pref:
        stations = [s for s in stations if s["pref"] == pref]
    return jsonify(stations)


@app.route("/api/prefs")
def api_prefs():
    """都道府県の一覧（地点が存在するもののみ、五十音/地域順ではなく登場順）。"""
    seen = []
    seen_set = set()
    for s in _station_master():
        if s["pref"] not in seen_set:
            seen_set.add(s["pref"])
            seen.append(s["pref"])
    return jsonify(seen)


@app.route("/api/search")
def api_search():
    """地点名・都道府県名によるインクリメンタル検索。全国約1,300地点でも
    軽量なマスタのみを対象に走査するため高速に応答する。
    """
    q = request.args.get("q", "").strip()
    pref = request.args.get("pref", "").strip()
    limit = max(1, min(int(request.args.get("limit", 30)), 100))

    idx = _search_index()
    items = idx["by_pref"].get(pref, idx["stations"]) if pref else idx["stations"]

    if q:
        q_lower = q.lower()
        starts, contains = [], []
        for s in items:
            name_l = s["name"].lower()
            if name_l.startswith(q_lower):
                starts.append(s)
            elif q in s["name"] or q in s["pref"] or q_lower in name_l:
                contains.append(s)
        items = starts + contains

    return jsonify(items[:limit])


@app.route("/api/ranking")
def api_ranking():
    rtype = request.args.get("type", "current")
    limit = int(request.args.get("limit", 20))
    key_map = {
        "current": "current_temp",
        "today_max": "today_max",
        "estimated_max": "estimated_max",
        "prob40": "prob_40",
        "pace": "pace_per_hour",
    }
    key = key_map.get(rtype, "current_temp")
    snap = get_snapshot()
    valid = [s for s in snap["stations"] if s.get(key) is not None]
    ranked = sorted(valid, key=lambda s: -s[key])[:limit]
    return jsonify({"type": rtype, "key": key, "items": ranked})


@app.route("/api/station/<sid>")
def api_station_detail(sid):
    now = datetime.now(JST)
    master = {s["id"]: s for s in _station_master()}
    if sid not in master:
        return jsonify({"error": "not_found"}), 404
    st = master[sid]

    if ad.USE_MOCK:
        snap = ad.build_mock_station_snapshot(st, now)
        upper = ad.build_mock_upper_air(st)
        year_high = round(max(st["normal_high"] + 5.5, snap["today_max"]), 1)
    else:
        try:
            tsdata = ad.fetch_point_timeseries(sid, now.date())
            series = _parse_point_series(tsdata, now.date())
            if not series:
                raise ValueError("empty series")
            current_temp = series[-1]["temp"]
            today_max = max(p["temp"] for p in series)
            today_max_time = next(p["time"] for p in series if p["temp"] == today_max)
            snap = {
                "series": series, "current_temp": current_temp, "today_max": today_max,
                "today_max_time": today_max_time, "target_max": today_max, "peak_hour": PEAK_HOUR,
                "humidity": None, "wind_speed": None, "wind_dir": None,
                "sunshine_min": None, "pressure": None, "precip": None,
            }
        except Exception:
            snap = ad.build_mock_station_snapshot(st, now)
        upper = {"t850": None, "t925": None, "t975": None, "analysis": "高層データ未取得"}
        year_high = round(snap["today_max"], 1)

    pace = pred.compute_pace(snap["series"])
    prediction = pred.predict(
        current_temp=snap["current_temp"], now=now, pace=pace,
        peak_hour=snap.get("peak_hour", PEAK_HOUR), record_high=st["record_high"],
        year_high=year_high, month_high=st.get("month_high"),
        humidity=snap.get("humidity"), wind_speed=snap.get("wind_speed"),
        sunshine_min=snap.get("sunshine_min"),
    )

    snapshot_all = get_snapshot()["stations"]
    # 詳細ページ表示中の地点の現況をランキング計算に反映(整合性のため上書き)
    for s in snapshot_all:
        if s["id"] == sid:
            s["current_temp"] = snap["current_temp"]
            s["estimated_max"] = prediction["estimated_max"]
    nat_rank, nat_total, pref_rank, pref_total = _rank_national_pref(snapshot_all, sid, "current_temp")

    if snap["current_temp"] >= 35:
        heat_level = "非常に高温" if snap["current_temp"] < 38 else ("記録級の危険な暑さ" if snap["current_temp"] < 40 else "歴代最高レベルの猛暑")
    elif snap["current_temp"] >= 30:
        heat_level = "真夏日の暑さ"
    else:
        heat_level = "平常範囲"

    return jsonify({
        "station": st,
        "current_temp": snap["current_temp"],
        "today_max": snap["today_max"],
        "today_max_time": snap["today_max_time"],
        "humidity": snap.get("humidity"),
        "wind_speed": snap.get("wind_speed"),
        "wind_dir": snap.get("wind_dir"),
        "sunshine_min": snap.get("sunshine_min"),
        "pressure": snap.get("pressure"),
        "precip": snap.get("precip"),
        "upper_air": upper,
        "pace": pace,
        "prediction": prediction,
        "year_high": year_high,
        "month_high": st.get("month_high"),
        "diff_from_record": round(st["record_high"] - snap["current_temp"], 1),
        "diff_from_year_high": round(year_high - snap["current_temp"], 1),
        "diff_from_month_high": round(st.get("month_high", year_high) - snap["current_temp"], 1) if st.get("month_high") is not None else None,
        "diff_from_normal": round(snap["current_temp"] - st["normal_high"], 1),
        "rank_national": nat_rank, "rank_national_total": nat_total,
        "rank_pref": pref_rank, "rank_pref_total": pref_total,
        "heat_level": heat_level,
        "generated_at": now.isoformat(),
    })


@app.route("/api/station/<sid>/series")
def api_station_series(sid):
    now = datetime.now(JST)
    master = {s["id"]: s for s in _station_master()}
    if sid not in master:
        return jsonify({"error": "not_found"}), 404
    st = master[sid]
    target = request.args.get("target", "today")

    if ad.USE_MOCK:
        if target == "yesterday":
            series = ad.build_mock_yesterday_series(st, now)
        elif target == "normal":
            series = ad.build_mock_normal_series(st, now)
        else:
            series = ad.build_mock_station_snapshot(st, now)["series"]
    else:
        try:
            if target == "yesterday":
                tsdata = ad.fetch_point_timeseries(sid, now.date() - timedelta(days=1))
                series = _parse_point_series(tsdata, now.date() - timedelta(days=1))
            elif target == "normal":
                series = ad.build_mock_normal_series(st, now)  # 平年値APIは別途要統合
            else:
                tsdata = ad.fetch_point_timeseries(sid, now.date())
                series = _parse_point_series(tsdata, now.date())
        except Exception:
            series = ad.build_mock_station_snapshot(st, now)["series"]

    # 推定曲線（現在時刻以降）も today の場合は付与
    projection = []
    if target == "today" and series:
        pace = pred.compute_pace(series)
        prediction = pred.predict(
            current_temp=series[-1]["temp"], now=now, pace=pace,
            peak_hour=PEAK_HOUR, record_high=st["record_high"],
        )
        last_t = datetime.fromisoformat(series[-1]["time"])
        last_v = series[-1]["temp"]
        steps = 12
        for i in range(1, steps + 1):
            frac = i / steps
            tt = last_t + timedelta(hours=prediction["hours_to_peak"] * frac) if prediction["hours_to_peak"] > 0 else last_t + timedelta(minutes=10 * i)
            vv = last_v + (prediction["estimated_max"] - last_v) * frac
            projection.append({"time": tt.isoformat(), "temp": round(vv, 1)})

    return jsonify({"target": target, "series": series, "projection": projection})


@app.route("/api/compare")
def api_compare():
    ids = [i.strip() for i in request.args.get("ids", "").split(",") if i.strip()]
    ids = ids[:4]
    now = datetime.now(JST)
    master = {s["id"]: s for s in _station_master()}
    results = []
    for sid in ids:
        if sid not in master:
            continue
        st = master[sid]
        if ad.USE_MOCK:
            snap = ad.build_mock_station_snapshot(st, now)
        else:
            try:
                tsdata = ad.fetch_point_timeseries(sid, now.date())
                series = _parse_point_series(tsdata, now.date())
                snap = {"series": series, "current_temp": series[-1]["temp"], "today_max": max(p["temp"] for p in series),
                        "humidity": None, "wind_speed": None, "peak_hour": PEAK_HOUR}
            except Exception:
                snap = ad.build_mock_station_snapshot(st, now)
        pace = pred.compute_pace(snap["series"])
        prediction = pred.predict(
            current_temp=snap["current_temp"], now=now, pace=pace,
            peak_hour=snap.get("peak_hour", PEAK_HOUR), record_high=st["record_high"],
            month_high=st.get("month_high"),
            humidity=snap.get("humidity"), wind_speed=snap.get("wind_speed"),
            sunshine_min=snap.get("sunshine_min"),
        )
        snapshot_all = get_snapshot()["stations"]
        nat_rank, nat_total, pref_rank, pref_total = _rank_national_pref(snapshot_all, sid, "current_temp")
        results.append({
            "station": st,
            "series": snap["series"],
            "current_temp": snap["current_temp"],
            "today_max": snap["today_max"],
            "humidity": snap.get("humidity"),
            "wind_speed": snap.get("wind_speed"),
            "pace_per_hour": pace["pace_per_hour"],
            "estimated_max": prediction["estimated_max"],
            "prob_40": prediction["prob_40"],
            "rank_national": nat_rank,
            "rank_national_total": nat_total,
            "rank_pref": pref_rank,
            "rank_pref_total": pref_total,
        })
    return jsonify(results)


@app.after_request
def _compress_response(response):
    """全国約1,300地点分のJSONは数百KBになり得るため、対応ブラウザにはgzip圧縮して返す。
    （読み込み高速化。flask-compress等の追加依存を増やさず標準ライブラリのみで実装）
    """
    accept_encoding = request.headers.get("Accept-Encoding", "")
    if (
        "gzip" not in accept_encoding
        or response.direct_passthrough
        or response.status_code < 200
        or response.status_code >= 300
        or len(response.get_data()) < 800
        or "Content-Encoding" in response.headers
    ):
        return response
    compressed = gzip.compress(response.get_data(), compresslevel=6)
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    response.headers.setdefault("Vary", "Accept-Encoding")
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
