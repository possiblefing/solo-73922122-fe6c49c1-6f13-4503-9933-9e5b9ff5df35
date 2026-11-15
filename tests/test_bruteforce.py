"""用穷举枚举交叉校验 DP 的最优性与“事实/待核”判定。

穷举方式：对每个站枚举所有非空圈内区间，回溯搜索所有“站次递增、区间不交、
归站连续”的归属，直接得到全局最优集合，再与 DP 结果逐一比对。
"""

from __future__ import annotations

import itertools
import random
from datetime import datetime, timedelta, timezone

from app.analysis import analyze, dwell_intervals, haversine_m
from app.models import Position, Stop

BASE_TIME = datetime(2026, 11, 15, 8, 0, 0, tzinfo=timezone.utc)
BASE_LAT = 30.0
BASE_LON = 120.0


def make_positions(offsets: list[float]) -> list[Position]:
    return [
        Position(
            index=i,
            time=BASE_TIME + timedelta(seconds=30 * i),
            lat=BASE_LAT + off,
            lon=BASE_LON,
        )
        for i, off in enumerate(offsets)
    ]


def brute_force(stops: list[Stop], positions: list[Position]):
    """返回 (最优 (站数, 点数), {stop_index: set(可行区间)})。"""

    masks = {
        s.index: [
            haversine_m(s.lat, s.lon, p.lat, p.lon) <= s.radius_m + 1e-9 for p in positions
        ]
        for s in stops
    }
    intervals = {s.index: dwell_intervals(masks[s.index]) for s in stops}
    order = [s.index for s in stops]
    best: tuple[int, int] | None = None
    best_choices: list[dict[int, tuple[int, int]]] = []

    def record(choice: dict[int, tuple[int, int]]) -> None:
        nonlocal best
        key = (len(choice), sum(b - a + 1 for a, b in choice.values()))
        if best is None or key > best:
            best = key
            best_choices.clear()
            best_choices.append(dict(choice))
        elif key == best:
            best_choices.append(dict(choice))

    def walk(pos_in_order: int, next_free: int, chosen: dict[int, tuple[int, int]]) -> None:
        if pos_in_order == len(order):
            record(chosen)
            return
        # 跳过该站
        walk(pos_in_order + 1, next_free, chosen)
        stop_id = order[pos_in_order]
        for a, b in intervals[stop_id]:
            if a < next_free:
                continue
            chosen[stop_id] = (a, b)
            walk(pos_in_order + 1, b + 1, chosen)
            del chosen[stop_id]

    walk(0, 0, {})

    per_stop: dict[int, set[tuple[int, int]]] = {}
    in_all: dict[int, bool] = {}
    for s in stops:
        windows = {c[s.index] for c in best_choices if s.index in c}
        per_stop[s.index] = windows
        in_all[s.index] = bool(windows) and all(s.index in c for c in best_choices)
    return best, per_stop, in_all


def random_scenario(rng: random.Random, *, n_stops: int, n_points: int) -> tuple[list[Stop], list[Position]]:
    """沿一条近似直线布置站点与定位点，刻意制造圈重叠与竞争。"""

    # 沿纬度方向等距布置站点；半径随机，足以与邻站重叠
    spacing = rng.uniform(0.4, 1.2) * 1e-3
    stops = [
        Stop(
            index=i,
            name=f"S{i}",
            lat=BASE_LAT + i * spacing + rng.uniform(-0.05, 0.05) * 1e-3,
            lon=BASE_LON + rng.uniform(-0.05, 0.05) * 1e-3,
            radius_m=rng.choice([40, 70, 110, 160]),
        )
        for i in range(n_stops)
    ]
    # 定位点沿同一条线前进，偶尔抖动，偶尔停顿（连续贴近同一站）
    points = []
    lat = BASE_LAT - 0.2e-3 + rng.uniform(-0.1, 0.1) * 1e-3
    for j in range(n_points):
        if rng.random() < 0.45:
            jitter = rng.uniform(-0.03, 0.03) * 1e-3
        else:
            lat += spacing * rng.uniform(0.3, 0.9)
            jitter = rng.uniform(-0.08, 0.08) * 1e-3
        points.append(
            Position(
                index=j,
                time=BASE_TIME + timedelta(minutes=j),
                lat=lat + jitter,
                lon=BASE_LON + rng.uniform(-0.03, 0.03) * 1e-3,
            )
        )
    return stops, points


def test_dp_matches_brute_force_on_random_cases() -> None:
    rng = random.Random(20261115)
    compared = 0
    pending_cases = 0
    multi_stop_cases = 0
    for _ in range(400):
        n_stops = rng.randint(1, 4)
        n_points = rng.randint(2, 9)
        stops, points = random_scenario(rng, n_stops=n_stops, n_points=n_points)
        best, per_stop, in_all = brute_force(stops, points)
        try:
            analysis = analyze(stops, points)
        except Exception as exc:  # noqa: BLE001 - 只允许“无可用停靠”
            assert best is None or best[0] == 0, f"DP 报错但穷举有解: {exc}"
            assert getattr(exc, "code", "") == "no_dwell"
            continue

        assert (analysis.optimal_stops, analysis.optimal_points) == best
        for report in analysis.reports:
            brute_windows = per_stop[report.stop.index]
            assert set(report.intervals) == brute_windows, (
                f"站 {report.stop.index} 区间不一致: DP={report.intervals} BF={sorted(brute_windows)}"
            )
            expected_status = "not_stopped"
            if brute_windows:
                expected_status = (
                    "fact" if in_all[report.stop.index] and len(brute_windows) == 1 else "pending"
                )
            assert report.status == expected_status
        pending_cases += sum(1 for r in analysis.reports if r.status == "pending")
        multi_stop_cases += 1 if analysis.optimal_stops >= 2 else 0
        compared += 1
    assert compared > 150
    # 保证交叉校验确实覆盖了这两类关键情形
    assert pending_cases > 20, f"待核样本过少: {pending_cases}"
    assert multi_stop_cases > 20, f"多站样本过少: {multi_stop_cases}"


def test_dp_matches_brute_force_no_dwell_cases() -> None:
    rng = random.Random(7)
    no_dwell = 0
    for _ in range(120):
        stops, points = random_scenario(rng, n_stops=rng.randint(1, 3), n_points=2)
        best, _, _ = brute_force(stops, points)
        try:
            analyze(stops, points)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", "") == "no_dwell"
            assert best is None or best[0] == 0
            no_dwell += 1
        else:
            assert best is not None and best[0] > 0
    assert no_dwell > 0
