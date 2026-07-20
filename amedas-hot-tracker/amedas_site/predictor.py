# -*- coding: utf-8 -*-
"""
predictor.py
============
現在までの気温推移から

  * 昇温速度（30分変化・1時間変化）
  * 推定最高気温とその予測区間
  * ピーク到達予想時刻
  * 40℃到達確率
  * 記録更新確率

を算出するヒューリスティック予測ロジック。

考え方:
  1. 直近1〜2時間の観測から昇温ペース(℃/時)を最小二乗で推定する。
  2. 典型的なピーク時刻(peak_hour、デフォルト14:30)までの残り時間に対して、
     「昇温ペースは時間が経つほど鈍る」性質をダンピング係数で表現し、
     残り上昇量を積算する。
  3. 直近の変動幅(標準偏差)を用いて、残り時間が長いほど広がる予測区間を作る。
  4. 40℃到達確率・記録更新確率は、推定最高気温と予測区間から
     正規分布近似で「しきい値を超える確率」を計算する（標準正規CDF）。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta


def _erf(x: float) -> float:
    # Abramowitz-Stegun近似（標準ライブラリのmath.erfで十分だが明示しておく）
    return math.erf(x)


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 1e-6:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / (sigma * math.sqrt(2))
    return 0.5 * (1 + _erf(z))


def _prob_exceeds(threshold: float, mu: float, sigma: float) -> float:
    """推定最高気温が threshold 以上になる確率 = 1 - CDF(threshold)"""
    p = 1 - _norm_cdf(threshold, mu, sigma)
    return max(0.0, min(1.0, p))


def compute_pace(series: list[dict]) -> dict:
    """直近時系列から30分変化・1時間変化・回帰による1時間あたりの昇温ペースを求める。"""
    if len(series) < 2:
        return {"pace_per_hour": 0.0, "delta_30m": 0.0, "delta_1h": 0.0, "volatility": 0.3}

    def temp_at_minutes_ago(minutes: int):
        target = datetime.fromisoformat(series[-1]["time"]) - timedelta(minutes=minutes)
        best = None
        best_diff = None
        for p in series:
            t = datetime.fromisoformat(p["time"])
            diff = abs((t - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = p["temp"]
        return best

    now_temp = series[-1]["temp"]
    t30 = temp_at_minutes_ago(30)
    t60 = temp_at_minutes_ago(60)
    delta_30 = round(now_temp - t30, 1) if t30 is not None else 0.0
    delta_60 = round(now_temp - t60, 1) if t60 is not None else 0.0

    # 直近120分を対象に最小二乗回帰で ℃/時 を求める
    window = []
    cutoff = datetime.fromisoformat(series[-1]["time"]) - timedelta(minutes=120)
    for p in series:
        t = datetime.fromisoformat(p["time"])
        if t >= cutoff:
            window.append(((t - cutoff).total_seconds() / 3600.0, p["temp"]))
    if len(window) >= 2:
        n = len(window)
        sx = sum(x for x, _ in window)
        sy = sum(y for _, y in window)
        sxx = sum(x * x for x, _ in window)
        sxy = sum(x * y for x, y in window)
        denom = (n * sxx - sx * sx)
        slope = (n * sxy - sx * sy) / denom if abs(denom) > 1e-9 else 0.0
    else:
        slope = 0.0

    # ボラティリティ = 直近差分の標準偏差(予測区間の幅に利用)
    diffs = []
    for i in range(1, len(window)):
        diffs.append(window[i][1] - window[i - 1][1])
    if len(diffs) >= 2:
        mean_d = sum(diffs) / len(diffs)
        var = sum((d - mean_d) ** 2 for d in diffs) / (len(diffs) - 1)
        volatility = max(0.15, min(1.5, math.sqrt(var) * 3))
    else:
        volatility = 0.35

    return {
        "pace_per_hour": round(slope, 2),
        "delta_30m": delta_30,
        "delta_1h": delta_60,
        "volatility": round(volatility, 2),
    }


def _weather_adjust_factor(
    humidity: float | None, wind_speed: float | None, sunshine_min: float | None
) -> float:
    """湿度・風速・日照から、残り昇温量に掛ける補正係数(1.0=補正なし)を求める。

    考え方(簡易ヒューリスティック):
      - 湿度が高い: 雲量・水蒸気により日射加熱が鈍りがち → 係数を下げる
      - 湿度が低い: 乾燥晴天で放射加熱が効きやすい → 係数を上げる
      - 風速が強い: 上空との混合で気温が跳ね上がりにくい → 係数を下げる
      - 風速が弱い: 地表付近に熱がこもりやすい → 係数を上げる
      - 直近日照が長い: 日射があり昇温を後押し → 係数を上げる
      - 直近日照が短い(曇天等): 昇温が鈍る → 係数を下げる
    """
    factor = 1.0
    if humidity is not None:
        if humidity >= 75:
            factor -= 0.15
        elif humidity <= 40:
            factor += 0.08
    if wind_speed is not None:
        if wind_speed >= 5:
            factor -= 0.12
        elif wind_speed <= 1.5:
            factor += 0.05
    if sunshine_min is not None:
        if sunshine_min >= 50:
            factor += 0.10
        elif sunshine_min <= 15:
            factor -= 0.15
    return max(0.55, min(1.35, factor))


def _similar_day_correction(slope: float) -> float:
    """類似日補正: 直近の昇温ペースが「平年並みの立ち上がり」から
    どれだけ乖離しているかに応じて、推定最高気温を微調整する簡易ロジック。
    本来は過去の類似気象日から統計的に補正するのが望ましいが、
    ここでは基準ペース(0.6℃/時)との差分を目安として反映する。
    """
    typical_pace = 0.6
    correction = (slope - typical_pace) * 0.3
    return round(max(-0.5, min(0.5, correction)), 2)


def predict(
    current_temp: float,
    now: datetime,
    pace: dict,
    peak_hour: float = 14.5,
    record_high: float | None = None,
    year_high: float | None = None,
    month_high: float | None = None,
    humidity: float | None = None,
    wind_speed: float | None = None,
    sunshine_min: float | None = None,
) -> dict:
    now_hour = now.hour + now.minute / 60.0
    hours_to_peak = max(0.0, peak_hour - now_hour)

    slope = pace["pace_per_hour"]
    adjust_factor = _weather_adjust_factor(humidity, wind_speed, sunshine_min)

    if hours_to_peak <= 0.05:
        # ピーク時間帯：ペースが正ならわずかに残り上昇を見込む
        remaining_rise = max(0.0, slope) * 0.25 * adjust_factor
    else:
        # 時間が経つほど昇温ペースは鈍化する、というダンピング
        damping = 1.0 / (1.0 + 0.22 * hours_to_peak)
        remaining_rise = max(-1.0, slope) * hours_to_peak * damping * adjust_factor
        remaining_rise = max(remaining_rise, 0.0 if slope <= 0 else remaining_rise)

    similar_day_adjust = _similar_day_correction(slope)
    estimated_max = current_temp + remaining_rise + similar_day_adjust
    estimated_max = round(estimated_max, 1)

    sigma = pace["volatility"] * math.sqrt(max(0.3, hours_to_peak + 0.3))
    if humidity is None and wind_speed is None and sunshine_min is None:
        # 気象要素が使えない場合は不確実性が高いぶん予測区間を広げる
        sigma *= 1.15
    sigma = round(min(sigma, 3.0), 2)

    low = round(estimated_max - sigma, 1)
    high = round(estimated_max + sigma, 1)

    prob_40 = round(_prob_exceeds(40.0, estimated_max, sigma) * 100, 1)

    prob_record = None
    diff_to_record = None
    if record_high is not None:
        diff_to_record = round(record_high - estimated_max, 1)
        prob_record = round(_prob_exceeds(record_high, estimated_max, sigma) * 100, 1)

    diff_to_year_high = None
    if year_high is not None:
        diff_to_year_high = round(year_high - estimated_max, 1)

    prob_month = None
    diff_to_month_high = None
    if month_high is not None:
        diff_to_month_high = round(month_high - estimated_max, 1)
        prob_month = round(_prob_exceeds(month_high, estimated_max, sigma) * 100, 1)

    # ピーク到達予想時刻：昇温ペースが強いほど peak_hour よりやや遅め、
    # 既にペースが鈍っていれば早めに前倒しする簡易補正
    peak_adjust = 0.0
    if slope > 1.2:
        peak_adjust = min(0.6, (slope - 1.2) * 0.3)
    elif slope < 0.2:
        peak_adjust = -min(1.0, (0.2 - slope) * 1.2)
    adjusted_peak_hour = max(now_hour, min(17.0, peak_hour + peak_adjust))
    peak_h = int(adjusted_peak_hour)
    peak_m = int(round((adjusted_peak_hour - peak_h) * 60))
    if peak_m == 60:
        peak_h += 1
        peak_m = 0

    return {
        "estimated_max": estimated_max,
        "range_low": low,
        "range_high": high,
        "sigma": sigma,
        "prob_40": prob_40,
        "prob_record": prob_record,
        "diff_to_record": diff_to_record,
        "diff_to_year_high": diff_to_year_high,
        "prob_month": prob_month,
        "diff_to_month_high": diff_to_month_high,
        "similar_day_adjust": similar_day_adjust,
        "weather_adjust_factor": round(adjust_factor, 2),
        "peak_time_estimate": f"{peak_h:02d}:{peak_m:02d}",
        "hours_to_peak": round(hours_to_peak, 2),
    }


def temp_color_category(temp: float) -> str:
    if temp is None:
        return "unknown"
    if temp < 25:
        return "blue"
    if temp < 30:
        return "green"
    if temp < 35:
        return "yellow"
    if temp < 38:
        return "orange"
    if temp < 40:
        return "red"
    return "purple"
