"""停靠事实求解。

模型
----
* 站点按线路顺序编号 0..n-1；定位点按时间顺序编号 0..m-1。
* 对每个站点 s 预先算出“圈内点”，并展开为所有长度 >= 2 的连续区间。
* 一个可行归属 = 若干站点按线路顺序各取一个圈内连续区间，区间两两不交且
  按时间先后排列（因此站点次序不倒退、每站最多一次、每点至多归一站）。
* 目标按字典序最大化：(停靠站数, 归站点数)。
* 对每个站点，在所有最优归属中收集其可能区间：
    - 恰有一个区间且该站在每个最优归属中都出现 -> 事实；
    - 否则 -> 待核，列出全部可能到离站时段；
    - 不在任何最优归属中 -> 未停靠。

DP
--
F[s][p] = 用站点 s..n-1 与定位点 p..m-1 可取得的最优 (站数, 点数)。
G[s][p] = 用站点 0..s-1 与定位点 0..p-1 可取得的最优 (站数, 点数)。
区间 [a,b] 属于站点 s 的某个最优归属，当且仅当
    G[s][a] + (1, b-a+1) + F[s+1][b+1] == 全局最优。
该等式同时覆盖了前后两侧的最优性，因此不会漏掉等价最优归属。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from .models import (
    DISTANCE_EPSILON_M,
    EARTH_RADIUS_M,
    MIN_DWELL_POINTS,
    InputError,
    Position,
    Stop,
    utc_iso,
)

Pair = tuple[int, int]  # (停靠站数, 归站点数)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间大圆球面距离（米）。"""

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    h = min(1.0, max(0.0, h))
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _add(a: Pair, b: Pair) -> Pair:
    return (a[0] + b[0], a[1] + b[1])


def _runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """返回连续 True 段的 [start, end] 闭区间列表。"""

    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for idx, flag in enumerate(flags):
        if flag and start is None:
            start = idx
        elif not flag and start is not None:
            runs.append((start, idx - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def dwell_intervals(mask: Sequence[bool]) -> list[tuple[int, int]]:
    """把圈内标记展开为所有长度 >= 2 的连续区间。"""

    intervals: list[tuple[int, int]] = []
    for start, end in _runs(mask):
        for a in range(start, end):
            for b in range(a + 1, end + 1):
                intervals.append((a, b))
    return intervals


def _max_run(mask: Sequence[bool]) -> int:
    return max((end - start + 1 for start, end in _runs(mask)), default=0)


def _station_essential(
    route_position: int,
    prefix: list[list[Pair]],
    suffix: list[list[Pair]],
    optimal: Pair,
    m: int,
) -> bool:
    """该站是否出现在每一个最优归属中（去掉它最优值是否下降）。

    只需判断“是否存在一个达到全局最优、但不含该站”的归属，等价于
    max_p (G[route_position][p] + F[route_position+1][p]) == optimal，
    其中 p 为前后两段在时间上的切分点。
    """

    for p in range(m + 1):
        if _add(prefix[route_position][p], suffix[route_position + 1][p]) == optimal:
            return False
    return True


def _suffix_table(
    stop_ids: Sequence[int], intervals: dict[int, list[tuple[int, int]]], m: int
) -> list[list[Pair]]:
    """F[s][p]，s 为 stop_ids 的下标（0..len），p 为定位点下标（0..m）。"""

    count = len(stop_ids)
    table: list[list[Pair]] = [[(0, 0)] * (m + 1) for _ in range(count + 1)]
    for s in range(count - 1, -1, -1):
        row = table[s]
        nxt = table[s + 1]
        for p in range(m - 1, -1, -1):
            best = nxt[p]
            for a, b in intervals[stop_ids[s]]:
                if a < p:
                    continue
                tail = nxt[b + 1]
                candidate = (tail[0] + 1, tail[1] + (b - a + 1))
                if candidate > best:
                    best = candidate
            row[p] = best
    return table


def _prefix_table(
    stop_ids: Sequence[int], intervals: dict[int, list[tuple[int, int]]], m: int
) -> list[list[Pair]]:
    """G[s][p]，s 为 stop_ids 的前缀长度（0..len），p 为可用定位点个数（0..m+1）。"""

    count = len(stop_ids)
    table: list[list[Pair]] = [[(0, 0)] * (m + 1) for _ in range(count + 1)]
    for s in range(1, count + 1):
        row = table[s]
        prev = table[s - 1]
        stop_id = stop_ids[s - 1]
        for p in range(m + 1):
            best = prev[p]
            for a, b in intervals[stop_id]:
                if b >= p:
                    continue
                head = prev[a]
                candidate = (head[0] + 1, head[1] + (b - a + 1))
                if candidate > best:
                    best = candidate
            row[p] = best
    return table


@dataclass
class StationReport:
    stop: Stop
    max_run: int = 0
    in_any_optimal: bool = False
    in_all_optimal: bool = False
    status: str = "not_stopped"  # fact | pending | not_stopped
    intervals: list[tuple[int, int]] = field(default_factory=list)

    def status_label(self) -> str:
        return {"fact": "事实", "pending": "待核", "not_stopped": "未停靠"}[self.status]


@dataclass
class Analysis:
    stops: list[Stop]
    positions: list[Position]
    reports: list[StationReport]
    optimal_stops: int
    optimal_points: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        facts: list[dict] = []
        pending: list[dict] = []
        not_stopped: list[dict] = []
        for report in self.reports:
            base = {
                "route_index": report.stop.index + 1,
                "stop_index": report.stop.index,
                "name": report.stop.name,
                "lat": report.stop.lat,
                "lon": report.stop.lon,
                "radius_m": report.stop.radius_m,
                "max_consecutive_points_in_circle": report.max_run,
                "status": report.status,
                "status_label": report.status_label(),
            }
            if report.status == "fact":
                a, b = report.intervals[0]
                base.update(
                    {
                        "arrival": utc_iso(self.positions[a].time),
                        "departure": utc_iso(self.positions[b].time),
                        "arrival_utc": utc_iso(self.positions[a].time),
                        "departure_utc": utc_iso(self.positions[b].time),
                        "point_count": b - a + 1,
                        "point_indices": list(range(a, b + 1)),
                        "point_times": [utc_iso(self.positions[i].time) for i in range(a, b + 1)],
                    }
                )
                facts.append(base)
            elif report.status == "pending":
                windows = [
                    {
                        "arrival": utc_iso(self.positions[a].time),
                        "departure": utc_iso(self.positions[b].time),
                        "point_count": b - a + 1,
                        "point_indices": [a, b],
                    }
                    for a, b in report.intervals
                ]
                base.update(
                    {
                        "possible_windows": windows,
                        "possible_window_count": len(windows),
                        "arrival_range": {
                            "earliest": min(w["arrival"] for w in windows),
                            "latest": max(w["arrival"] for w in windows),
                        },
                        "departure_range": {
                            "earliest": min(w["departure"] for w in windows),
                            "latest": max(w["departure"] for w in windows),
                        },
                        "appears_in_all_optimal": report.in_all_optimal,
                        "reason": (
                            "该站在所有同等最优归属中都会停靠，但到离站时刻随归属不同而变化"
                            if report.in_all_optimal
                            else "该站只在部分同等最优归属中停靠，且到离站时刻不唯一"
                        ),
                    }
                )
                pending.append(base)
            else:
                base["reason"] = (
                    f"圈内连续定位点最多 {report.max_run} 个，不足 {MIN_DWELL_POINTS} 个"
                    if report.max_run < MIN_DWELL_POINTS
                    else "该站虽有可用停留，但纳入后会挤占其他站，无法出现在任何最优归属中"
                )
                not_stopped.append(base)

        assigned_indices: set[int] = set()
        for report in self.reports:
            if report.status == "fact":
                a, b = report.intervals[0]
                assigned_indices.update(range(a, b + 1))
        unassigned = [i for i in range(len(self.positions)) if i not in assigned_indices]

        return {
            "summary": {
                "station_count": len(self.stops),
                "point_count": len(self.positions),
                "optimal_stop_count": self.optimal_stops,
                "optimal_assigned_point_count": self.optimal_points,
                "fact_count": len(facts),
                "pending_count": len(pending),
                "not_stopped_count": len(not_stopped),
                "has_pending": bool(pending),
                "complete": not pending,
                "has_stop_facts": bool(facts),
            },
            "facts": facts,
            "pending": pending,
            "not_stopped": not_stopped,
            "points": [
                {
                    "index": p.index,
                    "time": utc_iso(p.time),
                    "raw_time": p.raw_time,
                    "lat": p.lat,
                    "lon": p.lon,
                    "assigned_in_all_facts": p.index in assigned_indices,
                }
                for p in self.positions
            ],
            "unassigned_point_indices": unassigned,
            "warnings": self.warnings,
            "criteria": "先最大化停靠站数，再最大化归站点数",
        }

    def facts_csv(self) -> str:
        """导出事实 CSV（UTF-8 BOM 便于 Excel 打开），不含待核内容。"""

        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(["线路站序", "站名", "纬度", "经度", "半径米", "到站时刻UTC", "离站时刻UTC", "归站点数"])
        for report in self.reports:
            if report.status != "fact":
                continue
            a, b = report.intervals[0]
            writer.writerow(
                [
                    report.stop.index + 1,
                    report.stop.name,
                    f"{report.stop.lat:.7f}",
                    f"{report.stop.lon:.7f}",
                    f"{report.stop.radius_m:g}",
                    utc_iso(self.positions[a].time),
                    utc_iso(self.positions[b].time),
                    b - a + 1,
                ]
            )
        return "﻿" + buffer.getvalue()


def analyze(stops: Sequence[Stop], positions: Sequence[Position], *, warnings: Optional[list[str]] = None) -> Analysis:
    """按规则求解；没有任何可用停靠时抛出 InputError，绝不返回部分事实。"""

    if not stops:
        raise InputError("没有站点，无法判断停靠。", code="no_stops")
    if not positions:
        raise InputError("没有定位点，无法判断停靠。", code="no_points")

    # 内部一律以“线路位置”为键，避免外部传入的 index 与顺序不一致
    stops = [replace(stop, index=position) for position, stop in enumerate(stops)]
    positions = [replace(position, index=order) for order, position in enumerate(positions)]

    m = len(positions)
    intervals: dict[int, list[tuple[int, int]]] = {}
    masks: dict[int, list[bool]] = {}
    for stop in stops:
        mask = [
            haversine_m(stop.lat, stop.lon, pos.lat, pos.lon)
            <= stop.radius_m + DISTANCE_EPSILON_M
            for pos in positions
        ]
        masks[stop.index] = mask
        intervals[stop.index] = dwell_intervals(mask)

    stop_ids = [stop.index for stop in stops]
    suffix = _suffix_table(stop_ids, intervals, m)
    prefix = _prefix_table(stop_ids, intervals, m)
    optimal = suffix[0][0]

    if optimal[0] == 0:
        details = []
        for stop in stops:
            run = _max_run(masks[stop.index])
            details.append(
                f"「{stop.name}」半径 {stop.radius_m:g} 米：圈内最多连续 {run} 个定位点。"
            )
        if m < MIN_DWELL_POINTS:
            details.insert(0, f"本趟只有 {m} 个定位点，至少需要 {MIN_DWELL_POINTS} 个连续定位点。")
        raise InputError(
            "没有可用停靠：没有任何站点能在圈内形成连续 "
            f"{MIN_DWELL_POINTS} 个及以上的原始定位点。",
            code="no_dwell",
            details=details,
        )

    reports: list[StationReport] = []
    for position_in_route, stop in enumerate(stops):
        mask = masks[stop.index]
        report = StationReport(stop=stop, max_run=_max_run(mask))

        chosen: list[tuple[int, int]] = []
        for a, b in intervals[stop.index]:
            total = _add(
                _add(prefix[position_in_route][a], (1, b - a + 1)),
                suffix[position_in_route + 1][b + 1],
            )
            if total == optimal:
                chosen.append((a, b))
        report.intervals = chosen
        report.in_any_optimal = bool(chosen)

        if not chosen:
            report.status = "not_stopped"
        else:
            report.in_all_optimal = _station_essential(
                position_in_route, prefix, suffix, optimal, m
            )
            if report.in_all_optimal and len(chosen) == 1:
                report.status = "fact"
            else:
                report.status = "pending"
        reports.append(report)

    return Analysis(
        stops=list(stops),
        positions=list(positions),
        reports=reports,
        optimal_stops=optimal[0],
        optimal_points=optimal[1],
        warnings=list(warnings or []),
    )
