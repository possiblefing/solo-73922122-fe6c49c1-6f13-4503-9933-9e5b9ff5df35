"""CSV 解析与校验测试。"""

from __future__ import annotations

import pytest

from app.models import InputError
from app.parsing import parse_inputs, parse_position_csv, parse_station_csv

STATION_HEADER = "站名,纬度,经度,半径米\n"
POSITION_HEADER = "时刻UTC,纬度,经度\n"


def stations(*rows: str) -> bytes:
    return (STATION_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def positions(*rows: str) -> bytes:
    return (POSITION_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def test_parse_station_and_position_basic() -> None:
    parsed = parse_inputs(
        stations("甲站,31.2,121.4,100", "乙站,31.3,121.5,80"),
        positions(
            "2026-11-15T08:00:00Z,31.2,121.4",
            "2026-11-15T08:01:00Z,31.2,121.4",
        ),
    )
    assert [s.name for s in parsed.stops] == ["甲站", "乙站"]
    assert parsed.stops[0].radius_m == 100
    assert len(parsed.positions) == 2
    assert parsed.positions[0].time.isoformat() == "2026-11-15T08:00:00+00:00"


def test_bom_and_gb18030_and_semicolon() -> None:
    station = "站名;纬度;经度;半径米\n甲站;31.2;121.4;100\n".encode("gb18030")
    stops, _ = parse_station_csv(station)
    assert stops[0].name == "甲站"

    position = "﻿时刻,纬度,经度\n2026-11-15 08:00:00,31.2,121.4\n".encode("utf-8")
    points, warnings = parse_position_csv(position)
    assert len(points) == 1
    assert any("UTC" in w for w in warnings)


def test_alt_header_names() -> None:
    station = "站点名称,latitude,longitude,站圈半径（米）\n甲站,31.2,121.4,100\n".encode()
    stops, _ = parse_station_csv(station)
    assert stops[0].name == "甲站"
    assert stops[0].radius_m == 100

    position = "timestamp,lat,lng\n2026-11-15T08:00:00Z,31.2,121.4\n".encode()
    points, _ = parse_position_csv(position)
    assert len(points) == 1


def test_radius_with_unit_suffix() -> None:
    stops, _ = parse_station_csv(stations("甲站,31.2,121.4,100m"))
    assert stops[0].radius_m == 100


def test_missing_column_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv("站名,纬度,半径米\n甲站,31.2,100\n".encode())
    assert excinfo.value.code == "missing_column"
    assert "经度" in excinfo.value.message


def test_duplicate_column_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv("站名,纬度,经度,半径米,纬度\n甲站,31.2,121.4,100,31.2\n".encode())
    assert excinfo.value.code == "duplicate_column"


def test_bad_number_reports_line() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(stations("甲站,abc,121.4,100"))
    assert excinfo.value.code == "bad_number"
    assert "第 2 行" in excinfo.value.message


def test_out_of_range_values() -> None:
    with pytest.raises(InputError):
        parse_station_csv(stations("甲站,91,121.4,100"))
    with pytest.raises(InputError):
        parse_station_csv(stations("甲站,31.2,181,100"))
    with pytest.raises(InputError):
        parse_station_csv(stations("甲站,31.2,121.4,0"))


def test_empty_name_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(stations(",31.2,121.4,100"))
    assert excinfo.value.code == "empty_name"


def test_too_many_stops_rejected() -> None:
    rows = [f"站{i},{31 + i / 100:.4f},121.4,100" for i in range(7)]
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(stations(*rows))
    assert excinfo.value.code == "too_many_stops"


def test_six_stops_allowed() -> None:
    rows = [f"站{i},{31 + i / 100:.4f},121.4,100" for i in range(6)]
    stops, _ = parse_station_csv(stations(*rows))
    assert len(stops) == 6


def test_too_many_points_rejected() -> None:
    rows = [f"2026-11-15T08:{i:02d}:00Z,31.2,121.4" for i in range(41)]
    # 00..40 分钟都合法时刻
    with pytest.raises(InputError) as excinfo:
        parse_position_csv(positions(*rows))
    assert excinfo.value.code == "too_many_points"


def test_forty_points_allowed() -> None:
    rows = [f"2026-11-15T08:{i // 60:02d}:{i % 60:02d}Z,31.2,121.4" for i in range(40)]
    points, _ = parse_position_csv(positions(*rows))
    assert len(points) == 40


def test_non_increasing_time_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_position_csv(
            positions(
                "2026-11-15T08:01:00Z,31.2,121.4",
                "2026-11-15T08:00:00Z,31.2,121.4",
            )
        )
    assert excinfo.value.code == "time_not_increasing"
    assert "严格递增" in excinfo.value.message


def test_duplicate_time_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_position_csv(
            positions(
                "2026-11-15T08:00:00Z,31.2,121.4",
                "2026-11-15T08:00:00Z,31.2,121.4",
            )
        )
    assert excinfo.value.code == "time_not_increasing"


def test_bad_time_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_position_csv(positions("昨天早上,31.2,121.4"))
    assert excinfo.value.code == "bad_time"


def test_empty_file_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(b"")
    assert excinfo.value.code == "empty_file"

    with pytest.raises(InputError):
        parse_station_csv(b"   \n\n")


def test_binary_file_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(b"\x00\x01\x02binary")
    assert excinfo.value.code == "binary_file"


def test_header_only_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(STATION_HEADER.encode())
    assert excinfo.value.code == "no_data"


def test_headerless_fallback_with_warning() -> None:
    stops, warnings = parse_station_csv("甲站,31.2,121.4,100\n乙站,31.3,121.5,80\n".encode())
    assert [s.name for s in stops] == ["甲站", "乙站"]
    assert any("固定列序" in w for w in warnings)


def test_row_too_short_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv(stations("甲站,31.2,121.4"))
    assert excinfo.value.code == "row_too_short"


def test_trailing_empty_cells_tolerated() -> None:
    stops, _ = parse_station_csv(stations("甲站,31.2,121.4,100,,"))
    assert len(stops) == 1


def test_unrelated_columns_ignored_with_warning() -> None:
    stops, warnings = parse_station_csv(
        "站名,纬度,经度,半径米,备注\n甲站,31.2,121.4,100,早班\n".encode()
    )
    assert len(stops) == 1
    assert any("备注" in w for w in warnings)


def test_quoted_comma_in_station_name() -> None:
    stops, _ = parse_station_csv(
        '站名,纬度,经度,半径米\n"人民广场, 东侧",31.239,121.47,90\n'.encode("gb18030")
    )
    assert stops[0].name == "人民广场, 东侧"
    assert stops[0].radius_m == 90


def test_ambiguous_header_rejected() -> None:
    with pytest.raises(InputError) as excinfo:
        parse_station_csv("站名,经纬度,半径米\n甲站,31.2,100\n".encode())
    assert excinfo.value.code == "ambiguous_header"
