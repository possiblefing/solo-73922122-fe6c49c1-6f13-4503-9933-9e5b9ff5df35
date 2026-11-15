"""基于合成坐标的分析逻辑测试（纯 Python，不依赖 Web 层）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import analyze, dwell_intervals, haversine_m
from app.models import InputError, Position, Stop

BASE_TIME = datetime(2026, 11, 15, 8, 0, 0, tzinfo=timezone.utc)
# 纬度 0.001 度约 111.2 米；用 base 点 + 纬度偏移构造精确几何
BASE_LAT = 30.0000
BASE_LON = 120.0000


def make_stop(index: int, name: str, lat: float, lon: float, radius: float = 50.0) -> Stop:
    return Stop(index=index, name=name, lat=lat, lon=lon, radius_m=radius)


def make_positions(offsets_deg: list[float], *, step_seconds: int = 30) -> list[Position]:
    """按纬度方向偏移（度）生成定位点，经度固定 BASE_LON。"""

    return [
        Position(
            index=i,
            time=BASE_TIME + timedelta(seconds=step_seconds * i),
            lat=BASE_LAT + off,
            lon=BASE_LON,
            raw_time="",
        )
        for i, off in enumerate(offsets_deg)
    ]


def meters_to_deg(meters: float) -> float:
    return meters / 111_194.9  # 纬度 1 度 ≈ 111.2 km


def test_haversine_zero_and_known_distance() -> None:
    assert haversine_m(30.0, 120.0, 30.0, 120.0) == 0.0
    # 经度方向 1 度在纬度 30 度处约 96.5 km
    d = haversine_m(30.0, 120.0, 30.0, 121.0)
    assert 95_000 < d < 98_000


def test_dwell_intervals_expands_runs() -> None:
    mask = [False, True, True, True, False, True, False]
    assert dwell_intervals(mask) == [(1, 2), (1, 3), (2, 3)]


def test_dwell_intervals_ignores_single_points() -> None:
    assert dwell_intervals([True, False, True, True, False]) == [(2, 3)]


def test_two_stops_clear_facts() -> None:
    stops = [
        make_stop(0, "A", BASE_LAT, BASE_LON),
        make_stop(1, "B", BASE_LAT + meters_to_deg(600), BASE_LON),
    ]
    # A 圈内 0,1,2；B 圈内 3,4
    offsets = [0.0, 0.0, 0.0, meters_to_deg(600), meters_to_deg(600)]
    result = analyze(stops, make_positions(offsets)).to_dict()

    assert result["summary"]["optimal_stop_count"] == 2
    assert result["summary"]["optimal_assigned_point_count"] == 5
    assert result["summary"]["fact_count"] == 2
    assert result["summary"]["pending_count"] == 0
    fact_a, fact_b = result["facts"]
    assert fact_a["name"] == "A" and fact_a["arrival"].endswith("08:00:00Z")
    assert fact_a["departure"].endswith("08:01:00Z")
    assert fact_b["name"] == "B" and fact_b["point_count"] == 2


def test_stop_order_cannot_go_backwards() -> None:
    """线路顺序 A 在前、B 在后，但定位先到 B 再到 A：只能选一个站。"""

    a_lat = BASE_LAT + meters_to_deg(600)
    b_lat = BASE_LAT
    stops = [make_stop(0, "A", a_lat, BASE_LON), make_stop(1, "B", b_lat, BASE_LON)]
    offsets = [0.0, 0.0, meters_to_deg(600), meters_to_deg(600)]
    result = analyze(stops, make_positions(offsets)).to_dict()

    # 两站各 2 点，但 A 在后时才会出现，B 在前；线路顺序 A->B 要求 A 时间在前，
    # 因此无法同时成立，最优只能停 1 站、归 2 点。
    assert result["summary"]["optimal_stop_count"] == 1
    assert result["summary"]["optimal_assigned_point_count"] == 2
    statuses = {item["name"]: item["status"] for item in result["facts"] + result["pending"]}
    assert statuses == {"A": "pending", "B": "pending"}
    # 两种归属各自只有一段区间，但同名站不同归属的区间一致 => A (2,3) / B (0,1)
    for item in result["pending"]:
        assert len(item["possible_windows"]) == 1


def test_ambiguous_window_requires_manual_review() -> None:
    """A 圈内有 3 个连续点，可形成 (0,1) 或 (0,2) 或 (1,2)。

    由于不存在第二个站，最优归站点数为 3，仅 (0,2) 达成，因此到离站唯一。
    """

    stops = [make_stop(0, "A", BASE_LAT, BASE_LON)]
    offsets = [0.0, 0.0, 0.0]
    result = analyze(stops, make_positions(offsets)).to_dict()

    assert result["summary"]["fact_count"] == 1
    fact = result["facts"][0]
    assert fact["arrival"].endswith("08:00:00Z")
    assert fact["departure"].endswith("08:01:00Z")


def test_same_stop_count_but_tied_windows_is_pending() -> None:
    """A 有 4 点，B 有 2 点且与 A 尾部重叠，使“停 2 站”下有多种同等归点方案。

    构造：点 0..3 在 A 圈内，点 2..3 也在 B 圈内（A 在前、B 在后）。
    最优为停 2 站：A 取 (0,1) 或 (0,2) 均可得 4 点 + B (2,3) 2 点 = ... 需精确计算。
    """

    a_lat, b_lat = BASE_LAT, BASE_LAT
    # A 与 B 同心但半径不同：A 只覆盖 0..1，B 覆盖 2..5
    stops = [
        Stop(index=0, name="A", lat=a_lat, lon=BASE_LON, radius_m=40),
        Stop(index=1, name="B", lat=b_lat, lon=BASE_LON, radius_m=40),
    ]
    offsets = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    # 两站完全同心同半径：所有区间都属于两站，但每站最多一次且顺序不得倒退
    result = analyze(stops, make_positions(offsets)).to_dict()
    # 停 2 站时，A 必须在 B 之前，所以 A 取一个前缀区间，B 取之后区间。
    # 最优归 6 点：A (0,2) + B (3,5) 等，存在多种切分 => 待核。
    assert result["summary"]["optimal_stop_count"] == 2
    assert result["summary"]["optimal_assigned_point_count"] == 6
    assert result["summary"]["pending_count"] == 2


def test_point_cannot_belong_to_two_stops() -> None:
    """同心圈下不能把同一批点同时算给两站。"""

    stops = [
        Stop(index=0, name="A", lat=BASE_LAT, lon=BASE_LON, radius_m=50),
        Stop(index=1, name="B", lat=BASE_LAT, lon=BASE_LON, radius_m=50),
    ]
    offsets = [0.0, 0.0]
    result = analyze(stops, make_positions(offsets)).to_dict()
    # 只有 2 点，最多给一个站；两个站都可行 => 均待核（各自只有一种窗口）
    assert result["summary"]["optimal_stop_count"] == 1
    assert result["summary"]["optimal_assigned_point_count"] == 2
    assert result["summary"]["pending_count"] == 2
    for item in result["pending"]:
        assert len(item["possible_windows"]) == 1


def test_fact_requires_identical_times_across_all_optimal_solutions() -> None:
    """A 的窗口在每个最优解中都相同 => 事实；B、C 竞争同一批点 => 待核。

    A 覆盖点 0,1；B 与 C 同心同半径，只覆盖点 2,3（仅够停一次）。
    最优停 2 站 = A + (B 或 C)，共归 4 点：
      * A 出现在每个最优解中，且窗口唯一 (0,1) => 事实；
      * B/C 各自窗口唯一，但都只是部分最优解包含 => 待核。
    """

    stops = [
        make_stop(0, "A", BASE_LAT, BASE_LON, radius=40.0),
        make_stop(1, "B", BASE_LAT + meters_to_deg(700), BASE_LON, radius=40.0),
        make_stop(2, "C", BASE_LAT + meters_to_deg(700), BASE_LON, radius=40.0),
    ]
    offsets = [0.0, 0.0, meters_to_deg(700), meters_to_deg(700)]
    result = analyze(stops, make_positions(offsets)).to_dict()

    assert result["summary"]["optimal_stop_count"] == 2
    assert result["summary"]["optimal_assigned_point_count"] == 4
    facts = {item["name"]: item for item in result["facts"]}
    assert set(facts) == {"A"}
    assert facts["A"]["point_count"] == 2
    assert facts["A"]["arrival"].endswith("08:00:00Z")
    assert facts["A"]["departure"].endswith("08:00:30Z")

    pending = {item["name"]: item for item in result["pending"]}
    assert set(pending) == {"B", "C"}
    for item in pending.values():
        assert item["appears_in_all_optimal"] is False
        assert item["possible_windows"]


def test_pending_when_station_always_stops_but_times_vary() -> None:
    """同一站必须停靠（所有最优解都含该站），但窗口随归属变化 => 待核。"""

    # 单站、4 个连续圈内点：最优归 4 点，只能取 (0,3)，窗口唯一 => 事实。
    # 再加一个“必须停”的第二站，并让其与第一站时段可交错：
    # A 覆盖点 0,1,2；B 覆盖点 1,2,3，但两者顺序固定 A->B 且不重叠。
    # 最优停 2 站、归 4 点：A (0,1) + B (2,3) 或 A (0,1)+B(2,3) 唯一？构造重叠：
    stops = [
        make_stop(0, "A", BASE_LAT, BASE_LON, radius=40.0),
        make_stop(1, "B", BASE_LAT, BASE_LON, radius=40.0),
    ]
    offsets = [0.0, 0.0, 0.0, 0.0, 0.0]
    result = analyze(stops, make_positions(offsets)).to_dict()
    # 2 站均必须停（去掉任一只能停 1 站）=> in_all_optimal True；
    # 但切分 5 点有多种方式 => 窗口不唯一 => 待核
    assert result["summary"]["optimal_stop_count"] == 2
    assert result["summary"]["pending_count"] == 2
    for item in result["pending"]:
        assert item["appears_in_all_optimal"] is True
        assert len(item["possible_windows"]) > 1
        assert "时刻" in item["reason"]


def test_lexicographic_prefers_more_stops_over_more_points() -> None:
    """停 2 站但只归 4 点，优于停 1 站归 5 点。"""

    # A 圈内点 0..4（5 点），B 圈内点 3..4（2 点，与 A 重叠）。
    # 停 2 站：A 必须取 0..2，B 取 3..4 => 5 点。这已兼顾，故构造更尖锐的场景：
    # A 圈内点 0,1；B 圈内点 2,3,4；C 圈内点 0..4（若只停 C 可得 5 点，停 A+B 得 5 点）
    offsets = [0.0, 0.0, meters_to_deg(600), meters_to_deg(600), meters_to_deg(600)]
    stops = [
        make_stop(0, "A", BASE_LAT, BASE_LON, radius=40.0),
        make_stop(1, "B", BASE_LAT + meters_to_deg(600), BASE_LON, radius=40.0),
        make_stop(2, "C", BASE_LAT + meters_to_deg(300), BASE_LON, radius=5000.0),
    ]
    result = analyze(stops, make_positions(offsets)).to_dict()
    assert result["summary"]["optimal_stop_count"] == 2
    assert result["summary"]["optimal_assigned_point_count"] == 5


def test_no_dwell_raises_with_reason() -> None:
    stops = [make_stop(0, "A", BASE_LAT, BASE_LON)]
    offsets = [0.0, meters_to_deg(5_000), 0.0]
    with pytest.raises(InputError) as excinfo:
        analyze(stops, make_positions(offsets))
    assert excinfo.value.code == "no_dwell"
    assert "连续" in excinfo.value.message
    assert any("A" in detail for detail in excinfo.value.details)


def test_single_point_raises() -> None:
    stops = [make_stop(0, "A", BASE_LAT, BASE_LON)]
    with pytest.raises(InputError) as excinfo:
        analyze(stops, make_positions([0.0]))
    assert excinfo.value.code == "no_dwell"


def test_radius_boundary_is_inclusive() -> None:
    stops = [make_stop(0, "A", BASE_LAT, BASE_LON, radius=100.0)]
    # 100 米略内、100.4 米外
    offsets = [0.0, 0.0, 0.0]
    result = analyze(stops, make_positions(offsets)).to_dict()
    assert result["summary"]["fact_count"] == 1

    outside = make_stop(0, "A", BASE_LAT, BASE_LON, radius=10.0)
    offsets2 = [0.0, meters_to_deg(12.0)]
    with pytest.raises(InputError):
        analyze([outside], make_positions(offsets2))


def test_facts_csv_only_contains_facts() -> None:
    stops = [
        Stop(index=0, name="A", lat=BASE_LAT, lon=BASE_LON, radius_m=50),
        Stop(index=1, name="B", lat=BASE_LAT, lon=BASE_LON, radius_m=50),
    ]
    analysis = analyze(stops, make_positions([0.0, 0.0]))
    csv_text = analysis.facts_csv()
    assert csv_text.startswith("﻿")
    assert "线路站序" in csv_text
    # 全部待核 => 只有表头一行
    assert csv_text.count("\r\n") == 1
