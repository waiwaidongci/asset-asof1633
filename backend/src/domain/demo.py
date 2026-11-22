"""桂桥往分站调拨的演示交簿：接收、正常配对、跨月在途、去向不符、实收晚到与重复上传。"""


_LINES = (
    # 单据编号, 工具编号, 工具名称, 交接日期, 顺序号, 单据类型, 发站, 收站
    ("RK-001", "GQ-01", "充电电钻", "2026-08-28", 1, "intake", None, "桂桥"),
    ("RK-002", "GQ-02", "数字万用表", "2026-08-28", 1, "intake", None, "桂桥"),
    ("RK-003", "GQ-03", "园艺修枝剪", "2026-08-31", 1, "intake", None, "桂桥"),
    ("RK-004", "GQ-04", "手持砂光机", "2026-09-03", 1, "intake", None, "桂桥"),
    ("DB-0901", "GQ-01", None, "2026-09-12", 1, "dispatch", "桂桥", "东风分站"),
    ("DB-0901", "GQ-01", None, "2026-09-14", 1, "receipt", "桂桥", "东风分站"),
    ("DB-0902", "GQ-02", None, "2026-09-30", 1, "dispatch", "桂桥", "南苑分站"),
    ("DB-0902", "GQ-02", None, "2026-10-02", 1, "receipt", "桂桥", "南苑分站"),
    ("DB-1001", "GQ-03", None, "2026-10-10", 1, "dispatch", "桂桥", "湖畔分站"),
    ("DB-1002", "GQ-04", None, "2026-10-12", 1, "dispatch", "桂桥", "东风分站"),
    ("DB-1002", "GQ-04", None, "2026-10-13", 1, "receipt", "桂桥", "东风分站"),
    ("RK-005", "GQ-05", "折叠踏梯", "2026-10-20", 1, "intake", None, "桂桥"),
    ("DB-1001", "GQ-03", None, "2026-11-01", 1, "receipt", "桂桥", "湖畔分站"),
    ("DB-1101", "GQ-05", None, "2026-11-05", 1, "dispatch", "桂桥", "东风分站"),
    ("DB-1101", "GQ-05", None, "2026-11-08", 1, "receipt", "桂桥", "东风分站"),
    ("DB-1102", "GQ-02", None, "2026-11-10", 1, "dispatch", "南苑分站", "桂桥"),
    ("DB-1102", "GQ-02", None, "2026-11-12", 1, "receipt", "南苑分站", "桂桥"),
    ("DB-1103", "GQ-01", None, "2026-11-15", 1, "dispatch", "东风分站", "桂桥"),
    ("DB-1103", "GQ-01", None, "2026-11-15", 2, "receipt", "东风分站", "南苑分站"),
    ("DB-1105", "GQ-06", "棘轮扳手套装", "2026-11-18", 1, "receipt", "桂桥", "湖畔分站"),
    ("RT-001", "GQ-05", None, "2026-11-19", 1, "retire", "东风分站", None),
    ("DB-1106", "GQ-04", None, "2026-11-20", 1, "dispatch", "东风分站", "南苑分站"),
)


def demo_rows() -> list[dict]:
    rows = [
        {"slip_no": slip_no, "asset_id": asset_id, "asset_name": asset_name,
         "handover_date": day, "sequence": sequence, "event_type": event_type,
         "from_station": from_station, "to_station": to_station}
        for slip_no, asset_id, asset_name, day, sequence, event_type, from_station, to_station in _LINES
    ]
    rows.append(rows[4].copy())
    return rows
