"""按交接日回放两站交簿，配对调出与实收，计算分站、在途与全站勾稽。"""

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from .spec import SPEC


_EVENT_LABELS = {"intake": "接收", "dispatch": "调出", "receipt": "实收", "retire": "退役"}
_STATUS_LABELS = {"on_hand": "在站", "in_transit": "在途", "retired": "退役", None: "未接收"}
_PENDING_LABELS = {
    "late": "实收单晚到，调出单未配齐",
    "mismatch": "单号对上但工具或去向不符",
    "date_conflict": "实收日早于调出日",
}
_PENDING_SHORT = {"late": "实收单晚到", "mismatch": "工具或去向不符", "date_conflict": "交接日矛盾"}
_PENDING_ADVICE = {
    "late": "补录发站调出单后重跑，按实际交接日补算",
    "mismatch": "核对两站单据；修正前涉事工具按调拨前状态记账",
    "date_conflict": "核对两单交接日期，修正后重跑",
}
_EXTERNAL = "外部"
_FIELD_BY_KEY = {field["key"]: field for field in SPEC["fields"]}
_PARAM_DEFAULTS = {field["key"]: field["default"] for field in SPEC["params"]}
_BALANCE_COLUMNS = (
    ("start_date", "统计首日"), ("as_of_date", "截止日"), ("close_type", "快照类型"),
    ("opening_stations", "期初各站"), ("opening_in_transit", "期初在途"), ("opening_active", "期初全站"),
    ("intake", "接收"), ("dispatched", "调出"), ("received", "实收"), ("retired", "退役"),
    ("closing_stations", "期末各站"), ("closing_in_transit", "期末在途"), ("closing_active", "期末全站"),
    ("total_difference", "各站在途差额"), ("active_difference", "全站勾稽差额"),
    ("transit_difference", "在途勾稽差额"), ("station_difference", "分站勾稽差额"),
)
_SEGMENT_COLUMNS = (
    ("segment", "环节"), ("opening", "期初"), ("inflow", "流入"), ("outflow", "流出"),
    ("retired", "退役"), ("closing", "期末"), ("difference", "勾稽差额"),
)
_ASSET_COLUMNS = (
    ("asset_id", "工具编号"), ("asset_name", "工具名称"), ("status", "期末状态"),
    ("segment", "所在环节"), ("last_handover_date", "末笔交接日"), ("last_slip_no", "末笔单据"),
    ("pending", "待核"), ("pending_reason", "待核原因"),
)
_PENDING_COLUMNS = (
    ("slip_no", "单据编号"), ("doc_type", "单据类型"), ("asset_id", "工具编号"),
    ("asset_name", "工具名称"), ("handover_date", "交接日期"), ("from_station", "发站"),
    ("to_station", "收站"), ("reason", "待核原因"), ("advice", "处置建议"),
)


@dataclass(frozen=True)
class _Line:
    slip_no: str
    asset_id: str
    asset_name: str | None
    handover_date: date
    sequence: int
    event_type: str
    from_station: str | None
    to_station: str | None


@dataclass(frozen=True)
class _Asset:
    name: str
    status: str
    station: str | None
    from_station: str | None
    to_station: str | None
    last_line: _Line


def _day(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}须为有效日期。") from None


def _line_text(row, key, index, *, required=True):
    field = _FIELD_BY_KEY[key]
    raw = row.get(key)
    if raw is None and not required:
        return None
    if isinstance(raw, str):
        value = raw.strip()
        if not value and not required:
            return None
        if value and len(value) <= field["max_length"]:
            return value
    raise ValueError(f"第 {index} 行的{field['label']}须为 1 至 {field['max_length']} 个字符。")


def _parse(rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("交接明细为空，请导入至少一条调拨单据。")
    lines = []
    sequence_field = _FIELD_BY_KEY["sequence"]
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 行单据格式有误。")
        kind = row.get("event_type")
        if kind not in _FIELD_BY_KEY["event_type"]["options"]:
            raise ValueError(f"第 {index} 行单据类型无效，须为 intake、dispatch、receipt 或 retire。")
        sequence = row.get("sequence")
        if type(sequence) is not int or not sequence_field["min"] <= sequence <= sequence_field["max"]:
            raise ValueError(f"第 {index} 行顺序号须为 1 至 {sequence_field['max']} 的整数。")
        from_station = _line_text(row, "from_station", index, required=kind in ("dispatch", "receipt", "retire"))
        to_station = _line_text(row, "to_station", index, required=kind in ("intake", "dispatch", "receipt"))
        if kind == "intake" and from_station is None:
            from_station = _EXTERNAL
        if kind == "retire" and to_station is not None:
            raise ValueError(f"第 {index} 行退役单不应填写收站。")
        if kind in ("dispatch", "receipt") and from_station == to_station:
            raise ValueError(f"第 {index} 行发站与收站相同，调拨须发生在两个站点之间。")
        lines.append(_Line(
            _line_text(row, "slip_no", index), _line_text(row, "asset_id", index),
            _line_text(row, "asset_name", index, required=kind == "intake"),
            _day(row.get("handover_date"), f"第 {index} 行交接日期"), sequence, kind,
            from_station, to_station,
        ))
    return lines


def _ordered_lines(rows):
    """校验格式，按「类型＋编号＋工具」去重并检查同日顺序号，返回排序后的单据行。"""
    by_key = {}
    by_slot = {}
    for line in _parse(rows):
        key = (line.event_type, line.slip_no, line.asset_id)
        previous = by_key.get(key)
        if previous == line:
            continue
        if previous is not None:
            raise ValueError(f"{_EVENT_LABELS[line.event_type]}单 {line.slip_no}（工具 {line.asset_id}）"
                             f"的内容冲突，请核对原始交簿。")
        slot = (line.asset_id, line.handover_date, line.sequence)
        if slot in by_slot:
            raise ValueError(f"工具 {line.asset_id} 在 {line.handover_date} 的顺序号 {line.sequence} 重复"
                             f"（{by_slot[slot]}、{line.slip_no}）。")
        by_slot[slot] = line.slip_no
        by_key[key] = line
    return sorted(by_key.values(), key=lambda line: (line.handover_date, line.asset_id, line.sequence))


def validate_rows(rows):
    """校验单据格式与重放键，返回去重后的规范单据；站点归属由盘点截止日决定。"""
    return [dict(asdict(line), handover_date=line.handover_date.isoformat()) for line in _ordered_lines(rows)]


def _pair(lines):
    """按单号配对调出与实收：晚到实收、工具或去向不符、日期矛盾的单据挂待核，不生效。"""
    dispatches = defaultdict(list)
    receipts = defaultdict(list)
    for line in lines:
        if line.event_type == "dispatch":
            dispatches[line.slip_no].append(line)
        elif line.event_type == "receipt":
            receipts[line.slip_no].append(line)
    pending = []
    for slip_no, slip_receipts in receipts.items():
        slip_dispatches = dispatches.get(slip_no, [])
        if not slip_dispatches:
            pending.extend((line, "late") for line in slip_receipts)
            continue
        by_routing = {(line.asset_id, line.from_station, line.to_station): line for line in slip_dispatches}
        by_asset = {line.asset_id: line for line in slip_dispatches}
        for receipt in slip_receipts:
            routing = (receipt.asset_id, receipt.from_station, receipt.to_station)
            dispatch = by_routing.get(routing)
            if dispatch is None:
                pending.append((receipt, "mismatch"))
                involved = by_asset.get(receipt.asset_id)
                if involved is not None:
                    pending.append((involved, "mismatch"))
            elif dispatch.handover_date > receipt.handover_date:
                pending.extend(((receipt, "date_conflict"), (dispatch, "date_conflict")))
    inactive = {id(line) for line, _ in pending}
    return [line for line in lines if id(line) not in inactive], pending


class _Ledger:
    def __init__(self, lines):
        self.assets = {}
        self.stations = Counter()
        self.in_transit = 0
        self.retired = 0
        self._lines = iter(lines)
        self._next = next(self._lines, None)

    def _apply(self, line):
        asset = self.assets.get(line.asset_id)
        prior = asset.status if asset else None
        station = asset.station if asset else None
        if line.event_type == "intake":
            ok = prior is None
            status, station = "on_hand", line.to_station
        elif line.event_type == "dispatch":
            ok = prior == "on_hand" and station == line.from_station
            status, station = "in_transit", None
        elif line.event_type == "receipt":
            ok = (prior == "in_transit" and asset.from_station == line.from_station
                  and asset.to_station == line.to_station)
            status, station = "on_hand", line.to_station
        else:
            ok = prior == "on_hand" and station == line.from_station
            status, station = "retired", None
        if not ok:
            where = {"on_hand": f"在{station}", "in_transit": "在途", "retired": "已退役"}.get(prior, "未接收")
            raise ValueError(f"{_EVENT_LABELS[line.event_type]}单 {line.slip_no}（工具 {line.asset_id}，"
                             f"{line.handover_date}）无法执行，该工具{where}。")
        if prior == "on_hand":
            self.stations[asset.station] -= 1
        elif prior == "in_transit":
            self.in_transit -= 1
        elif prior == "retired":
            self.retired -= 1
        if status == "on_hand":
            self.stations[station] += 1
        elif status == "in_transit":
            self.in_transit += 1
        else:
            self.retired += 1
        name = asset.name if asset else line.asset_name
        self.assets[line.asset_id] = _Asset(
            name, status, station,
            line.from_station if status == "in_transit" else None,
            line.to_station if status == "in_transit" else None,
            line,
        )

    def advance(self, boundary, *, inclusive=True):
        flows = {"intake": 0, "dispatch": 0, "receipt": 0, "retire": 0, "stations": defaultdict(Counter)}
        while (line := self._next) is not None:
            if line.handover_date > boundary or (line.handover_date == boundary and not inclusive):
                break
            self._apply(line)
            flows[line.event_type] += 1
            if line.event_type == "intake":
                flows["stations"][line.to_station]["intake"] += 1
            elif line.event_type == "dispatch":
                flows["stations"][line.from_station]["dispatch"] += 1
            elif line.event_type == "receipt":
                flows["stations"][line.to_station]["receipt"] += 1
            else:
                flows["stations"][line.from_station]["retire"] += 1
            self._next = next(self._lines, None)
        return flows

    def balance(self):
        stations = {station: count for station, count in self.stations.items() if count}
        return {"stations": stations, "in_transit": self.in_transit, "retired": self.retired,
                "active": sum(stations.values()) + self.in_transit}


def _merge_flows(total, part):
    for key in ("intake", "dispatch", "receipt", "retire"):
        total[key] += part[key]
    for station, counts in part["stations"].items():
        for key, value in counts.items():
            total["stations"][station][key] += value


def _balance_row(start, close, close_type, opening, closing, flows):
    opening_stations = sum(opening["stations"].values())
    closing_stations = sum(closing["stations"].values())
    intake, dispatched, received, retired = flows["intake"], flows["dispatch"], flows["receipt"], flows["retire"]
    return {
        "start_date": start.isoformat(), "as_of_date": close.isoformat(), "close_type": close_type,
        "opening_stations": opening_stations, "opening_in_transit": opening["in_transit"],
        "opening_active": opening["active"],
        "intake": intake, "dispatched": dispatched, "received": received, "retired": retired,
        "closing_stations": closing_stations, "closing_in_transit": closing["in_transit"],
        "closing_active": closing["active"],
        "total_difference": closing["active"] - closing_stations - closing["in_transit"],
        "active_difference": closing["active"] - (opening["active"] + intake - retired),
        "transit_difference": closing["in_transit"] - (opening["in_transit"] + dispatched - received),
        "station_difference": closing_stations - (opening_stations + intake + received - dispatched - retired),
    }


def _monthly_balances(ledger, start, cutoff):
    monthly = []
    totals = {"intake": 0, "dispatch": 0, "receipt": 0, "retire": 0, "stations": defaultdict(Counter)}
    while True:
        month_end = start.replace(day=monthrange(start.year, start.month)[1])
        close = min(month_end, cutoff)
        opening = ledger.balance()
        flows = ledger.advance(close)
        _merge_flows(totals, flows)
        close_type = "自然月末" if close == month_end else "截止日"
        monthly.append(_balance_row(start, close, close_type, opening, ledger.balance(), flows))
        if close == cutoff:
            return monthly, totals
        start = close + timedelta(days=1)


def _segment_rows(opening, closing, totals):
    names = sorted(set(opening["stations"]) | set(closing["stations"]) | set(totals["stations"]))
    rows = []
    for station in names:
        flows = totals["stations"].get(station, Counter())
        inflow = flows["intake"] + flows["receipt"]
        closing_count = closing["stations"].get(station, 0)
        opening_count = opening["stations"].get(station, 0)
        rows.append({
            "segment": station, "opening": opening_count, "inflow": inflow,
            "outflow": flows["dispatch"], "retired": flows["retire"], "closing": closing_count,
            "difference": closing_count - (opening_count + inflow - flows["dispatch"] - flows["retire"]),
        })
    rows.append({
        "segment": "在途", "opening": opening["in_transit"], "inflow": totals["dispatch"],
        "outflow": totals["receipt"], "retired": 0, "closing": closing["in_transit"],
        "difference": closing["in_transit"] - (opening["in_transit"] + totals["dispatch"] - totals["receipt"]),
    })
    rows.append({
        "segment": "全站合计", "opening": opening["active"], "inflow": totals["intake"],
        "outflow": 0, "retired": totals["retire"], "closing": closing["active"],
        "difference": closing["active"] - (opening["active"] + totals["intake"] - totals["retire"]),
    })
    return rows


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def analyze(rows, normalized_params):
    """生成期末工具台账、分站与在途勾稽、逐月盘点及待核单据。"""
    if not isinstance(normalized_params, dict) or normalized_params.keys() - _PARAM_DEFAULTS.keys():
        raise ValueError("盘点参数格式有误，请核对统计首日和盘点截止日。")
    params = {**_PARAM_DEFAULTS, **normalized_params}
    start = _day(params["start_date"], "统计首日")
    cutoff = _day(params["as_of_date"], "盘点截止日")
    if start > cutoff:
        raise ValueError("统计首日须早于或等于盘点截止日。")

    active_lines, pending_lines = _pair(_ordered_lines(rows))
    ledger = _Ledger(active_lines)
    ledger.advance(start, inclusive=False)
    opening = ledger.balance()
    monthly, totals = _monthly_balances(ledger, start, cutoff)
    closing = ledger.balance()
    period = _balance_row(start, cutoff, "区间期末", opening, closing, totals)

    visible_pending = [(line, reason) for line, reason in pending_lines if line.handover_date <= cutoff]
    pending_reasons = defaultdict(list)
    for line, reason in visible_pending:
        pending_reasons[line.asset_id].append(reason)
    assets = []
    for asset_id, asset in sorted(ledger.assets.items()):
        reasons = pending_reasons.pop(asset_id, None)
        segment = {"on_hand": asset.station, "retired": "—"}.get(
            asset.status, f"在途（{asset.from_station}→{asset.to_station}）")
        assets.append({
            "asset_id": asset_id, "asset_name": asset.name, "status": _STATUS_LABELS[asset.status],
            "segment": segment, "last_handover_date": asset.last_line.handover_date.isoformat(),
            "last_slip_no": asset.last_line.slip_no,
            "pending": "待核" if reasons else None,
            "pending_reason": "、".join(sorted({_PENDING_LABELS[reason] for reason in reasons})) if reasons else None,
        })
    for asset_id, reasons in sorted(pending_reasons.items()):
        latest = max((line for line, _ in visible_pending if line.asset_id == asset_id),
                     key=lambda line: (line.handover_date, line.sequence))
        assets.append({
            "asset_id": asset_id, "asset_name": latest.asset_name, "status": "未入账", "segment": "—",
            "last_handover_date": latest.handover_date.isoformat(), "last_slip_no": latest.slip_no,
            "pending": "待核",
            "pending_reason": "、".join(sorted({_PENDING_LABELS[reason] for reason in reasons})),
        })
    pending_rows = [
        {"slip_no": line.slip_no, "doc_type": _EVENT_LABELS[line.event_type], "asset_id": line.asset_id,
         "asset_name": line.asset_name, "handover_date": line.handover_date.isoformat(),
         "from_station": line.from_station, "to_station": line.to_station,
         "reason": _PENDING_LABELS[reason], "advice": _PENDING_ADVICE[reason]}
        for line, reason in sorted(visible_pending, key=lambda item: (item[0].handover_date, item[0].slip_no))
    ]

    stations = closing["active"] - closing["in_transit"]
    transit = closing["in_transit"]
    active = closing["active"]
    station_detail = "、".join(f"{station} {count} 件" for station, count in closing["stations"].items()) or "各站均无存量"
    pending_counts = Counter(reason for _, reason in visible_pending)
    pending_detail = "、".join(f"{_PENDING_SHORT[reason]} {count} 笔" for reason, count in sorted(pending_counts.items()))
    reconciled = all(row[key] == 0 for row in [period, *monthly]
                     for key in ("total_difference", "active_difference", "transit_difference", "station_difference"))
    return {
        "state": "ok" if active and reconciled and not pending_rows else "warning",
        "headline": (f"截至 {cutoff}，全站有效工具 {active} 件：各站 {stations} 件、在途 {transit} 件"
                     + (f"，待核单据 {len(pending_rows)} 笔。" if pending_rows else "。")),
        "note": f"统计期间为 {start} 至 {cutoff}，含首尾两日。调出当日离开发站，实收当日进入收站，在途仍计入全站有效资产。",
        "metrics": [
            {"label": "期末全站有效资产", "value": active, "detail": f"各站 {stations} 件 ＋ 在途 {transit} 件"},
            {"label": "期末各站合计", "value": stations, "detail": station_detail},
            {"label": "期末在途", "value": transit, "detail": "已调出未实收，仍计入全站"},
            {"label": "待核单据", "value": len(pending_rows), "detail": pending_detail or "无待核单据"},
        ],
        "tables": [
            _table("区间期初期末勾稽", _BALANCE_COLUMNS, [period]),
            _table("月末与截止日勾稽", _BALANCE_COLUMNS, monthly),
            _table("分站与在途勾稽", _SEGMENT_COLUMNS, _segment_rows(opening, closing, totals)),
            _table("期末工具台账", _ASSET_COLUMNS, assets),
            _table("待核单据", _PENDING_COLUMNS, pending_rows),
        ],
        "charts": [
            {"title": title, "kind": "line",
             "series": [{"label": row["as_of_date"], "value": row[key]} for row in monthly]}
            for title, key in (("各盘点日全站有效资产（件）", "closing_active"), ("各盘点日在途工具（件）", "closing_in_transit"))
        ],
        "sections": [{"title": "勾稽与待核复核", "text": (
            f"各站 {stations} 件加在途 {transit} 件，与全站 {active} 件核对"
            + ("相符。" if reconciled else "存在差额，请核对交簿。")
            + (f"待核 {len(pending_rows)} 笔（{pending_detail}），配齐或修正后重跑按实际交接日补算，不重复计入。"
               if pending_rows else "无待核单据。")
            + "站点流入＝接收＋实收调入、流出＝调出；在途流入＝调出、流出＝实收；全站仅接收流入、退役流出。"
        )}],
    }
