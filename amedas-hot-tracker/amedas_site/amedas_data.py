# -*- coding: utf-8 -*-
"""
amedas_data.py
==============
アメダス観測点マスタと観測データの取得を担当するモジュール。

2つの動作モードを持つ:

1. 実データモード (AMEDAS_MOCK=0)
   気象庁が公開しているアメダス用JSON (Yahoo!天気やtenki.jp等の民間天気サイトも
   利用している一般公開エンドポイント) にアクセスして最新観測値・station一覧・
   地点別の当日時系列を取得する。

2. モックモード (AMEDAS_MOCK=1, デフォルト)
   ネットワークにアクセスできない/しない環境でもサイト全体の動作を確認できるよう、
   全国の主要地点について「らしい」気温の日変化カーブを疑似生成する。
   本番運用では AMEDAS_MOCK=0 にし、必要なら MAJOR_STATIONS を
   fetch_station_list() が返す気象庁の全観測点(約900地点)に差し替えるとよい。

気象庁エンドポイント(参考・変更される可能性あり):
  station一覧   : https://www.jma.go.jp/bosai/amedas/const/amedastable.json
  最新観測時刻  : https://www.jma.go.jp/bosai/amedas/data/latest_time.txt
  全国最新観測  : https://www.jma.go.jp/bosai/amedas/data/map/{yyyymmddHHMMSS}.json
  地点別当日推移: https://www.jma.go.jp/bosai/amedas/data/point/{id}/{yyyymmdd}_{prefix}.json
"""
from __future__ import annotations

import os
import json
import math
import random
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")

USE_MOCK = os.environ.get("AMEDAS_MOCK", "1") != "0"
REQUEST_TIMEOUT = 8

BASE = "https://www.jma.go.jp/bosai/amedas"
STATION_LIST_URL = f"{BASE}/const/amedastable.json"
LATEST_TIME_URL = f"{BASE}/data/latest_time.txt"
LATEST_MAP_URL_TMPL = f"{BASE}/data/map/{{time}}.json"
POINT_URL_TMPL = f"{BASE}/data/point/{{sid}}/{{ymd}}_{{prefix}}.json"

_session = requests.Session()
_session.headers.update({"User-Agent": "amedas-hot-tracker/1.0 (+https://render.com)"})

# ---------------------------------------------------------------------------
# 主要地点マスタ（モードモード用サンプル。全国50地点強をカバー）
# record_high: 観測史上1位の気温(目安値。デモ用の概算であり正式な記録は
#              気象庁「歴代全国ランキング」で要確認)
# normal_high: 8月頃の平年の日最高気温の目安
# ---------------------------------------------------------------------------
MAJOR_STATIONS = [
    # id, name, pref, lat, lon, alt(m), record_high, normal_high
    ("11001", "旭川", "北海道", 43.7706, 142.3649, 120, 36.7, 26.2),
    ("14163", "札幌", "北海道", 43.0621, 141.3544, 17, 36.3, 26.4),
    ("12442", "帯広", "北海道", 42.9237, 143.1961, 41, 37.8, 25.9),
    ("31312", "青森", "青森県", 40.8222, 140.7747, 3, 38.2, 27.5),
    ("32133", "盛岡", "岩手県", 39.7015, 141.1533, 155, 37.7, 27.9),
    ("33431", "仙台", "宮城県", 38.2688, 140.8721, 38, 37.3, 28.4),
    ("34133", "秋田", "秋田県", 39.7186, 140.1024, 6, 38.7, 28.6),
    ("35426", "山形", "山形県", 38.2554, 140.3392, 153, 40.8, 29.7),
    ("36127", "福島", "福島県", 37.7527, 140.4675, 67, 39.1, 29.9),
    ("40201", "水戸", "茨城県", 36.3781, 140.4715, 29, 38.4, 29.7),
    ("41277", "宇都宮", "栃木県", 36.5551, 139.8828, 119, 39.9, 30.4),
    ("42251", "前橋", "群馬県", 36.4053, 139.0608, 112, 40.0, 30.9),
    ("42041", "館林", "群馬県", 36.2453, 139.5461, 27, 40.3, 31.3),
    ("43056", "熊谷", "埼玉県", 36.1476, 139.3853, 30, 41.1, 31.4),
    ("43241", "さいたま", "埼玉県", 35.8912, 139.6294, 16, 39.8, 30.8),
    ("44132", "東京", "東京都", 35.6896, 139.7546, 25, 39.5, 29.9),
    ("44116", "青梅", "東京都", 35.7877, 139.2764, 187, 40.8, 30.5),
    ("45212", "千葉", "千葉県", 35.6034, 140.1233, 3, 39.5, 30.4),
    ("46106", "横浜", "神奈川県", 35.4437, 139.6380, 39, 37.4, 29.6),
    ("48156", "甲府", "山梨県", 35.6644, 138.5686, 273, 40.7, 30.9),
    ("50331", "長野", "長野県", 36.6513, 138.1810, 418, 38.9, 28.9),
    ("54232", "岐阜", "岐阜県", 35.4111, 136.7593, 13, 40.0, 31.1),
    ("54142", "多治見", "岐阜県", 35.3327, 137.1333, 90, 40.7, 30.9),
    ("56227", "静岡", "静岡県", 34.9756, 138.3831, 14, 38.7, 30.0),
    ("56136", "浜松", "静岡県", 34.7108, 137.7267, 33, 41.1, 30.5),
    ("51106", "名古屋", "愛知県", 35.1656, 136.9066, 51, 40.3, 30.9),
    ("55102", "津", "三重県", 34.7185, 136.5056, 6, 38.9, 30.4),
    ("60216", "新潟", "新潟県", 37.9161, 139.0364, 3, 38.7, 28.6),
    ("61286", "富山", "富山県", 36.6953, 137.2136, 9, 39.5, 29.3),
    ("62078", "金沢", "石川県", 36.5947, 136.6256, 5, 38.4, 29.1),
    ("63106", "福井", "福井県", 36.0621, 136.2217, 9, 38.6, 29.6),
    ("63518", "彦根", "滋賀県", 35.2747, 136.2597, 87, 38.8, 29.5),
    ("61099", "京都", "京都府", 35.0117, 135.7359, 41, 39.8, 30.9),
    ("62078a", "大阪", "大阪府", 34.6813, 135.5200, 23, 39.1, 30.8),
    ("64036", "神戸", "兵庫県", 34.6913, 135.1830, 5, 37.5, 29.7),
    ("64100", "豊岡", "兵庫県", 35.5433, 134.8203, 4, 39.9, 29.6),
    ("65042", "奈良", "奈良県", 34.6851, 135.8047, 104, 38.8, 30.1),
    ("65106", "和歌山", "和歌山県", 34.2306, 135.1706, 21, 38.6, 29.9),
    ("65356", "かつらぎ", "和歌山県", 34.3092, 135.5169, 33, 40.6, 30.4),
    ("66408", "鳥取", "鳥取県", 35.5039, 134.2358, 7, 38.8, 28.9),
    ("68132", "松江", "島根県", 35.4564, 133.0486, 11, 38.8, 29.3),
    ("66408b", "岡山", "岡山県", 34.6606, 133.9186, 4, 38.8, 30.8),
    ("67437", "広島", "広島県", 34.3963, 132.4658, 4, 38.5, 30.4),
    ("81401", "山口", "山口県", 34.1785, 131.4737, 5, 38.6, 29.9),
    ("71106", "徳島", "徳島県", 34.0658, 134.5594, 3, 38.4, 30.5),
    ("72086", "高松", "香川県", 34.3211, 134.0464, 4, 37.9, 30.5),
    ("73166", "松山", "愛媛県", 33.8422, 132.7656, 32, 37.6, 30.6),
    ("74181", "高知", "高知県", 33.5622, 133.5511, 0, 38.7, 30.6),
    ("74371", "江川崎", "高知県", 33.2308, 132.8619, 111, 41.0, 30.4),
    ("82182", "福岡", "福岡県", 33.5822, 130.3756, 3, 38.3, 30.8),
    ("85142", "佐賀", "佐賀県", 33.2635, 130.3009, 5, 38.7, 31.0),
    ("84496", "長崎", "長崎県", 32.7333, 129.8642, 27, 37.9, 29.9),
    ("86141", "熊本", "熊本県", 32.8039, 130.7075, 37, 38.9, 30.6),
    ("87376", "大分", "大分県", 33.2382, 131.6126, 3, 38.7, 30.2),
    ("87141", "日田", "大分県", 33.3211, 130.9414, 43, 39.9, 29.8),
    ("88317", "宮崎", "宮崎県", 31.9364, 131.4239, 8, 38.6, 30.5),
    ("88836", "鹿児島", "鹿児島県", 31.5533, 130.5497, 4, 38.6, 31.0),
    ("91197", "那覇", "沖縄県", 26.2072, 127.6858, 27, 35.6, 31.5),
]

# ---------------------------------------------------------------------------
# 全国約1,300地点対応
# ---------------------------------------------------------------------------
# 気象庁の観測点マスタ(amedastable.json)には都道府県名や平年値・記録値が
# 含まれていないため、以下の概算ロジックで補完する。
#   - 都道府県: 47都道府県庁所在地の緯度経度への最近傍判定（簡易・概算）
#   - 平年値/記録値: MAJOR_STATIONS(実況込み59地点)を教師データとした
#     逆距離加重(IDW)による補間＋標高補正
# いずれも正式な統計値ではなく目安値。より正確にする場合は気象庁「平年値」
# CSV等の正式データに差し替えること。
# ---------------------------------------------------------------------------

PREF_CENTROIDS = [
    ("北海道", 43.06, 141.35), ("青森県", 40.82, 140.74), ("岩手県", 39.70, 141.15),
    ("宮城県", 38.27, 140.87), ("秋田県", 39.72, 140.10), ("山形県", 38.26, 140.34),
    ("福島県", 37.75, 140.47), ("茨城県", 36.34, 140.45), ("栃木県", 36.57, 139.88),
    ("群馬県", 36.39, 139.06), ("埼玉県", 35.86, 139.65), ("千葉県", 35.60, 140.12),
    ("東京都", 35.69, 139.69), ("神奈川県", 35.45, 139.64), ("新潟県", 37.90, 139.02),
    ("富山県", 36.70, 137.21), ("石川県", 36.59, 136.63), ("福井県", 36.07, 136.22),
    ("山梨県", 35.66, 138.57), ("長野県", 36.65, 138.18), ("岐阜県", 35.39, 136.72),
    ("静岡県", 34.98, 138.38), ("愛知県", 35.18, 136.91), ("三重県", 34.73, 136.51),
    ("滋賀県", 35.00, 135.87), ("京都府", 35.02, 135.76), ("大阪府", 34.69, 135.52),
    ("兵庫県", 34.69, 135.18), ("奈良県", 34.69, 135.83), ("和歌山県", 34.23, 135.17),
    ("鳥取県", 35.50, 134.24), ("島根県", 35.47, 133.05), ("岡山県", 34.66, 133.93),
    ("広島県", 34.40, 132.46), ("山口県", 34.19, 131.47), ("徳島県", 34.07, 134.56),
    ("香川県", 34.34, 134.04), ("愛媛県", 33.84, 132.77), ("高知県", 33.56, 133.53),
    ("福岡県", 33.61, 130.42), ("佐賀県", 33.25, 130.30), ("長崎県", 32.75, 129.87),
    ("熊本県", 32.79, 130.74), ("大分県", 33.24, 131.61), ("宮崎県", 31.91, 131.42),
    ("鹿児島県", 31.56, 130.56), ("沖縄県", 26.21, 127.68),
]


def _nearest_pref(lat: float, lon: float) -> str:
    """緯度経度から最も近い都道府県庁所在地を返す（簡易な都道府県推定）。"""
    best_name, best_d = None, None
    for name, plat, plon in PREF_CENTROIDS:
        d = (lat - plat) ** 2 + (lon - plon) ** 2
        if best_d is None or d < best_d:
            best_d, best_name = d, name
    return best_name


def _idw_estimate(lat: float, lon: float, field: str) -> float:
    """MAJOR_STATIONS を教師点とした逆距離加重(IDW)補間。"""
    weights = []
    for sid, name, pref, slat, slon, salt, record_high, normal_high in MAJOR_STATIONS:
        d2 = (lat - slat) ** 2 + (lon - slon) ** 2
        w = 1.0 / max(d2, 1e-6)
        val = record_high if field == "record_high" else normal_high
        weights.append((w, val))
    sw = sum(w for w, _ in weights)
    return sum(w * v for w, v in weights) / sw


def estimate_normals(lat: float, lon: float, alt: float) -> tuple[float, float]:
    """未収録地点の平年値・記録的高温の目安を IDW補間＋標高補正で概算する。
    戻り値: (normal_high, record_high) いずれも概算値。"""
    normal = _idw_estimate(lat, lon, "normal_high")
    record = _idw_estimate(lat, lon, "record_high")
    alt_adjust = -0.6 * (max(alt, 0) / 100.0)  # 標高100mあたり-0.6℃程度の簡易補正
    normal = round(normal + alt_adjust, 1)
    record = round(max(record + alt_adjust, normal + 3.0), 1)
    return normal, record


STATION_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".station_cache.json")
STATION_CACHE_TTL_SEC = 24 * 3600  # 観測点マスタは頻繁に変わらないため24時間キャッシュ

_full_station_master_memo: list[dict] | None = None


def _parse_jma_degmin(pair) -> float:
    """気象庁JSONの [度, 分] 形式を10進度に変換する。"""
    deg, minute = pair[0], pair[1]
    return round(deg + minute / 60.0, 5)


def _build_full_station_list_from_jma(raw: dict) -> list[dict]:
    """amedastable.json の生データを、本サイトで使う地点マスタ形式に変換する。"""
    out = []
    for sid, info in raw.items():
        try:
            lat = _parse_jma_degmin(info["lat"])
            lon = _parse_jma_degmin(info["lon"])
        except (KeyError, TypeError, IndexError, ZeroDivisionError):
            continue
        elems = info.get("elems", "")
        # elems の1桁目が気温観測要素のフラグ（0=気温計なし雨量観測所等）。
        # 判定できない場合は除外せず含める（保守的にフォールバック）。
        if elems and len(elems) > 0 and elems[0] == "0":
            continue
        alt = info.get("alt", 0) or 0
        name = info.get("kjName") or info.get("enName") or sid
        pref = _nearest_pref(lat, lon)
        normal_high, record_high = estimate_normals(lat, lon, alt)
        out.append({
            "id": sid, "name": name, "pref": pref,
            "lat": lat, "lon": lon, "alt": alt,
            "record_high": record_high, "normal_high": normal_high,
            "month_high": round(normal_high + 2.0, 1),
            "estimated_values": True,  # 平年値/記録値が概算であることを示すフラグ
        })
    out.sort(key=lambda s: s["id"])
    return out


def _load_station_cache() -> list[dict] | None:
    try:
        if not os.path.exists(STATION_CACHE_PATH):
            return None
        with open(STATION_CACHE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if time.time() - payload.get("fetched_at", 0) > STATION_CACHE_TTL_SEC:
            return None
        stations = payload.get("stations")
        return stations if stations else None
    except Exception:
        return None


def _save_station_cache(stations: list[dict]) -> None:
    try:
        with open(STATION_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "stations": stations}, f, ensure_ascii=False)
    except Exception:
        pass  # キャッシュ書き込みに失敗しても動作継続（読み取り専用環境等）


def _fallback_station_master() -> list[dict]:
    """内蔵の主要地点(約59地点)を地点マスタ形式に変換したフォールバック。"""
    return [
        {"id": sid, "name": name, "pref": pref, "lat": lat, "lon": lon,
         "alt": alt, "record_high": rh, "normal_high": nh,
         "month_high": round(nh + 2.0, 1), "estimated_values": False}
        for sid, name, pref, lat, lon, alt, rh, nh in MAJOR_STATIONS
    ]


def get_full_station_master(force_refresh: bool = False) -> list[dict]:
    """全国のアメダス観測点マスタ(気温観測地点、約1,300地点)を返す。

    優先順位: 1) プロセス内メモリキャッシュ 2) ディスクキャッシュ(24h)
              3) 気象庁への実取得 4) 内蔵59地点へのフォールバック
    ネットワークに一度もアクセスできない環境（オフライン検証等）でも
    サイト全体が必ず動作するよう、最終手段としてフォールバックを保証する。
    """
    global _full_station_master_memo
    if not force_refresh and _full_station_master_memo is not None:
        return _full_station_master_memo

    if not force_refresh:
        cached = _load_station_cache()
        if cached:
            _full_station_master_memo = cached
            return cached

    try:
        raw = fetch_station_list()
        stations = _build_full_station_list_from_jma(raw)
        if len(stations) < 100:
            raise ValueError("station list too small; likely malformed response")
        _save_station_cache(stations)
        _full_station_master_memo = stations
        return stations
    except Exception:
        fallback = _fallback_station_master()
        _full_station_master_memo = fallback
        return fallback


# ---------------------------------------------------------------------------
# 実データモード
# ---------------------------------------------------------------------------

def fetch_station_list() -> dict:
    """気象庁のアメダス観測点一覧を取得する。失敗時は例外を投げる。"""
    r = _session.get(STATION_LIST_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_latest_time() -> str:
    r = _session.get(LATEST_TIME_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text.strip()


def fetch_latest_map(time_str: str) -> dict:
    """time_str は latest_time.txt の内容(ISO8601)をそのまま利用する。"""
    dt = datetime.fromisoformat(time_str.replace("Z", "+00:00")).astimezone(JST)
    key = dt.strftime("%Y%m%d%H%M%S")
    url = LATEST_MAP_URL_TMPL.format(time=key)
    r = _session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_point_timeseries(station_id: str, day: date) -> dict:
    ymd = day.strftime("%Y%m%d")
    prefix = station_id[:2]
    url = POINT_URL_TMPL.format(sid=station_id, ymd=ymd, prefix=prefix)
    r = _session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# モックデータ生成
# ---------------------------------------------------------------------------

def _rng_for(key: str) -> random.Random:
    seed = abs(hash((key, datetime.now(JST).strftime("%Y%m%d")))) % (2**31)
    return random.Random(seed)


def _diurnal_curve(t_hours: float, t_min: float, t_max: float, peak_hour: float) -> float:
    """0時=最低気温付近、peak_hour前後に最高気温となる簡易日変化カーブ。"""
    # 気温の目安として cos カーブを利用(谷=夜明け前, 山=peak_hour)
    trough_hour = 5.0
    span = t_max - t_min
    phase = (t_hours - trough_hour) / (24.0)
    # peak_hour に山が来るよう非対称に補正
    if t_hours <= trough_hour:
        frac = 0.0
    else:
        rise_len = peak_hour - trough_hour
        fall_len = 24 - peak_hour + trough_hour
        if t_hours <= peak_hour:
            frac = 0.5 - 0.5 * math.cos(math.pi * (t_hours - trough_hour) / rise_len)
        else:
            frac = 0.5 + 0.5 * math.cos(math.pi * (t_hours - peak_hour) / fall_len)
    return t_min + span * frac


def _station_target_max(st: dict, rng: random.Random) -> float:
    """本日の「本来の」最高気温の目安を、平年値+ランダムな猛暑偏差で決める。"""
    normal = st["normal_high"]
    # 猛暑バイアス：デモとして今日は全国的にかなり暑い設定にする
    heat_bias = rng.uniform(4.0, 9.5)
    noise = rng.uniform(-1.2, 1.2)
    target = normal + heat_bias + noise
    # 稀に記録に迫る猛烈な暑さにする地点を作る
    if rng.random() < 0.12:
        target = max(target, st["record_high"] - rng.uniform(-0.6, 1.5))
    return round(target, 1)


def build_mock_station_snapshot(st: dict, now: datetime) -> dict:
    """1地点分の「現在時刻までの時系列＋現在値」を生成する。"""
    rng = _rng_for(st["id"])
    peak_hour = float(os.environ.get("PEAK_HOUR", "14.5")) + rng.uniform(-0.7, 0.7)
    t_min = st["normal_high"] - rng.uniform(10, 13)
    t_max_target = _station_target_max(st, rng)

    now_hour = now.hour + now.minute / 60.0
    series = []
    walk_noise = 0.0
    t = 0.0
    while t <= now_hour + 1e-6:
        base = _diurnal_curve(t, t_min, t_max_target, peak_hour)
        # 累積ランダムウォークで観測っぽいギザギザを付与
        walk_noise = 0.85 * walk_noise + rng.uniform(-0.18, 0.18)
        val = round(base + walk_noise, 1)
        ts = (datetime.combine(now.date(), datetime.min.time(), tzinfo=JST) + timedelta(hours=t))
        series.append({"time": ts.isoformat(), "temp": val})
        t += 1 / 6  # 10分刻み

    current_temp = series[-1]["temp"] if series else t_min
    today_max = max(p["temp"] for p in series) if series else current_temp
    today_max_time = next(p["time"] for p in series if p["temp"] == today_max)

    # その他観測要素（簡易生成）
    humidity = max(20, min(95, round(75 - (current_temp - t_min) * 2.6 + rng.uniform(-6, 6))))
    wind_speed = round(max(0.0, rng.uniform(0.5, 6.0)), 1)
    wind_dir = rng.choice(["北", "北東", "東", "南東", "南", "南西", "西", "北西"])
    sunshine_min = round(max(0, min(60, (t - 6) * 8 + rng.uniform(-10, 10))), 0) if t < 18 else 0
    pressure = round(1006 + rng.uniform(-4, 4), 1)
    precip = 0.0 if rng.random() > 0.05 else round(rng.uniform(0.5, 4.0), 1)

    return {
        "series": series,
        "current_temp": current_temp,
        "today_max": today_max,
        "today_max_time": today_max_time,
        "target_max": t_max_target,
        "peak_hour": peak_hour,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "wind_dir": wind_dir,
        "sunshine_min": sunshine_min,
        "pressure": pressure,
        "precip": precip,
    }


def build_mock_yesterday_series(st: dict, now: datetime) -> list:
    """比較表示用の「昨日の気温推移」（1日分フル）を生成する。"""
    rng = _rng_for(st["id"] + "_yesterday")
    peak_hour = float(os.environ.get("PEAK_HOUR", "14.5")) + rng.uniform(-0.7, 0.7)
    t_min = st["normal_high"] - rng.uniform(9, 13)
    t_max_target = st["normal_high"] + rng.uniform(1.0, 6.0)
    yday = now.date() - timedelta(days=1)
    series = []
    walk_noise = 0.0
    t = 0.0
    while t <= 24:
        base = _diurnal_curve(t, t_min, t_max_target, peak_hour)
        walk_noise = 0.85 * walk_noise + rng.uniform(-0.18, 0.18)
        val = round(base + walk_noise, 1)
        ts = (datetime.combine(yday, datetime.min.time(), tzinfo=JST) + timedelta(hours=t))
        series.append({"time": ts.isoformat(), "temp": val})
        t += 1 / 6
    return series


def build_mock_normal_series(st: dict, now: datetime) -> list:
    """平年値カーブ(参考線)を生成する。"""
    t_min = st["normal_high"] - 11
    t_max = st["normal_high"]
    series = []
    t = 0.0
    while t <= 24:
        val = round(_diurnal_curve(t, t_min, t_max, 14.0), 1)
        ts = (datetime.combine(now.date(), datetime.min.time(), tzinfo=JST) + timedelta(hours=t))
        series.append({"time": ts.isoformat(), "temp": val})
        t += 1 / 6
    return series


def build_mock_upper_air(st: dict) -> dict:
    rng = _rng_for(st["id"] + "_upper")
    t850 = round(18 + rng.uniform(-2, 6), 1)
    t925 = round(24 + rng.uniform(-2, 6), 1)
    t975 = round(28 + rng.uniform(-2, 6), 1)
    if t850 >= 21:
        level = "歴代最高レベルの暖気（記録的高温の目安）"
    elif t850 >= 18:
        level = "非常に高温な暖気が流入中"
    elif t850 >= 15:
        level = "平年よりかなり高い暖気"
    else:
        level = "平年並みの暖気"
    return {"t850": t850, "t925": t925, "t975": t975, "analysis": level}
