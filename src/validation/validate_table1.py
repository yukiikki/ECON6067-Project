"""Independently validate the completed Table I replication.

This script deliberately does not import the Table I implementation. It
reconstructs signals, deciles, overlapping portfolios, monthly returns, and
summary statistics with separate code. It writes one Markdown report and does
not modify research inputs or existing Table I outputs.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
J_VALUES = (3, 6, 9, 12)
K_VALUES = (3, 6, 9, 12)
SELECTED_FORMATIONS = (200112, 201312, 202511)
SELECTED_HOLDINGS = (200201, 201401, 202512)
TOLERANCE = 1e-12


@dataclass(frozen=True)
class Cohort:
    p1: tuple[int, ...]
    p10: tuple[int, ...]
    eligible_count: int
    decile_sizes: tuple[int, ...]


def month_index(yyyymm: int) -> int:
    year, month = divmod(int(yyyymm), 100)
    if year < 1900 or not 1 <= month <= 12:
        raise ValueError(f"Invalid YYYYMM: {yyyymm}")
    return year * 12 + month - 1


def yyyymm_from_index(value: int) -> int:
    year, zero_month = divmod(value, 12)
    return year * 100 + zero_month + 1


def month_start(value: int) -> date:
    year, zero_month = divmod(value, 12)
    return date(year, zero_month + 1, 1)


def subtract_months(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 - months
    year, zero_month = divmod(total, 12)
    day = min(value.day, calendar.monthrange(year, zero_month + 1)[1])
    return date(year, zero_month + 1, day)


def sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def compound(values: list[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value
    return wealth - 1.0


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def read_validation_inputs(prepared: Path, monthly_lookup: Path):
    table = pq.read_table(prepared, columns=["permno", "yyyymm", "month"])
    data = table.to_pydict()
    eligibility: dict[int, list[int]] = defaultdict(list)
    prepared_keys: set[tuple[int, int]] = set()
    for permno, yyyymm, standard_month in zip(
        data["permno"], data["yyyymm"], data["month"], strict=True
    ):
        index = month_index(yyyymm)
        if standard_month != month_start(index):
            raise RuntimeError(f"Invalid Stage 2 month mapping: {(permno, yyyymm)}")
        key = (int(permno), index)
        if key in prepared_keys:
            raise RuntimeError(f"Duplicate Stage 2 key: {key}")
        prepared_keys.add(key)
        eligibility[index].append(int(permno))

    table = pq.read_table(
        monthly_lookup,
        columns=["permno", "yyyymm", "mth_ret", "mth_del_flg", "return_usable"],
    )
    data = table.to_pydict()
    by_stock: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    by_month: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    for permno, yyyymm, value, usable, flag in zip(
        data["permno"], data["yyyymm"], data["mth_ret"],
        data["return_usable"], data["mth_del_flg"], strict=True,
    ):
        index = month_index(yyyymm)
        observation = (value, bool(usable), flag or "")
        if index in by_stock[int(permno)]:
            raise RuntimeError(f"Duplicate monthly lookup key: {(permno, yyyymm)}")
        by_stock[int(permno)][index] = observation
        by_month[index][int(permno)] = observation
    return dict(eligibility), dict(by_stock), dict(by_month), prepared_keys


def panel_a_signals(
    eligibility: dict[int, list[int]],
    by_stock: dict[int, dict[int, tuple[float | None, bool, str]]],
    j: int,
) -> dict[int, list[tuple[int, float]]]:
    result: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for formation, permnos in eligibility.items():
        months = tuple(range(formation-j+1, formation+1))
        for permno in permnos:
            observations = [by_stock.get(permno, {}).get(month) for month in months]
            if any(observation is None for observation in observations):
                continue
            if not all(observation[1] for observation in observations if observation):
                continue
            values = [observation[0] for observation in observations if observation]
            if any(value is None for value in values):
                raise RuntimeError("A return marked usable is missing")
            result[formation].append((permno, compound([float(v) for v in values])))
    return dict(result)


def market_dates(connection: duckdb.DuckDBPyConnection, daily_cumulative: Path) -> list[date]:
    rows = connection.execute(
        f"SELECT DISTINCT trading_date FROM read_parquet('{sql_path(daily_cumulative)}') "
        "ORDER BY trading_date"
    ).fetchall()
    return [row[0] for row in rows]


def last_market_on_or_before(dates: list[date], cutoff: date) -> date | None:
    position = bisect.bisect_right(dates, cutoff) - 1
    return dates[position] if position >= 0 else None


def first_market_in_month(dates: list[date], month: int) -> date | None:
    start = month_start(month)
    position = bisect.bisect_left(dates, start)
    if position == len(dates):
        return None
    candidate = dates[position]
    if (candidate.year, candidate.month) != (start.year, start.month):
        return None
    return candidate


def build_panel_b_candidates(
    eligibility: dict[int, list[int]], dates: list[date]
) -> tuple[pa.Table, dict[tuple[int, int], tuple[date, date, date]]]:
    columns: dict[str, list] = {
        "permno": [], "formation": [], "j": [], "start_date": [], "end_date": []
    }
    boundaries: dict[tuple[int, int], tuple[date, date, date]] = {}
    for formation, permnos in eligibility.items():
        formation_date = first_market_in_month(dates, formation+1)
        if formation_date is None:
            continue
        end_date = last_market_on_or_before(dates, formation_date-timedelta(days=7))
        if end_date is None:
            continue
        for j in J_VALUES:
            start_date = last_market_on_or_before(dates, subtract_months(end_date, j))
            if start_date is None:
                continue
            boundaries[(formation, j)] = (formation_date, start_date, end_date)
            columns["permno"].extend(permnos)
            columns["formation"].extend([formation] * len(permnos))
            columns["j"].extend([j] * len(permnos))
            columns["start_date"].extend([start_date] * len(permnos))
            columns["end_date"].extend([end_date] * len(permnos))
    table = pa.table({
        "permno": pa.array(columns["permno"], pa.int64()),
        "formation": pa.array(columns["formation"], pa.int32()),
        "j": pa.array(columns["j"], pa.int8()),
        "start_date": pa.array(columns["start_date"], pa.date32()),
        "end_date": pa.array(columns["end_date"], pa.date32()),
    })
    return table, boundaries


def panel_b_signals(
    connection: duckdb.DuckDBPyConnection,
    candidates: pa.Table,
    daily_cumulative: Path,
) -> dict[int, dict[int, list[tuple[int, float]]]]:
    connection.register("validation_b_candidates", candidates)
    path = sql_path(daily_cumulative)
    query = f"""
        SELECT c.permno, c.formation, c.j, c.start_date, c.end_date,
               e.trading_date AS end_match, s.trading_date AS start_match,
               e.cumulative_log_return AS end_log,
               s.cumulative_log_return AS start_log,
               e.cumulative_valid_returns AS end_count,
               s.cumulative_valid_returns AS start_count
        FROM validation_b_candidates c
        LEFT JOIN read_parquet('{path}') e
          ON c.permno=e.permno AND c.end_date=e.trading_date
        LEFT JOIN read_parquet('{path}') s
          ON c.permno=s.permno AND c.start_date=s.trading_date
        ORDER BY c.formation, c.j, c.permno
    """
    result: dict[int, dict[int, list[tuple[int, float]]]] = {
        j: defaultdict(list) for j in J_VALUES
    }
    reader = connection.execute(query).to_arrow_reader(100_000)
    for batch in reader:
        data = batch.to_pydict()
        for row in zip(*(data[name] for name in batch.schema.names), strict=True):
            (
                permno, formation, j, start_date, end_date, end_match,
                start_match, end_log, start_log, end_count, start_count,
            ) = row
            if end_match != end_date or start_match != start_date:
                continue
            if int(end_count)-int(start_count) <= 0:
                continue
            signal = math.exp(float(end_log)-float(start_log))-1.0
            result[int(j)][int(formation)].append((int(permno), signal))
    connection.unregister("validation_b_candidates")
    return {j: dict(months) for j, months in result.items()}


def rank_deciles(signals: list[tuple[int, float]]) -> tuple[dict[int, tuple[int, ...]], dict[str, float]]:
    if len(signals) < 10:
        raise RuntimeError("Too few stocks for deciles")
    if len({permno for permno, _ in signals}) != len(signals):
        raise RuntimeError("Duplicate stock in formation universe")
    ordered = sorted(signals, key=lambda item: (item[1], item[0]))
    members: dict[int, list[int]] = {decile: [] for decile in range(1, 11)}
    signal_by_stock = dict(signals)
    for position, (permno, _) in enumerate(ordered):
        members[(position*10)//len(ordered)+1].append(permno)
    sizes = [len(members[d]) for d in range(1, 11)]
    if max(sizes)-min(sizes) > 1 or min(sizes) == 0:
        raise RuntimeError("Invalid decile sizes")
    if max(signal_by_stock[p] for p in members[1]) > min(
        signal_by_stock[p] for p in members[10]
    ):
        raise RuntimeError("Invalid P1/P10 order")
    weights = [math.fsum([1.0/len(members[d])] * len(members[d])) for d in range(1, 11)]
    return (
        {d: tuple(members[d]) for d in range(1, 11)},
        {
            "min_weight_sum": min(weights),
            "max_weight_sum": max(weights),
            "min_decile_size": min(sizes),
            "max_decile_size": max(sizes),
        },
    )


def make_cohorts(
    signals_by_month: dict[int, list[tuple[int, float]]],
    selected: set[int],
) -> tuple[dict[int, Cohort], dict[int, dict[int, tuple[int, ...]]], dict[str, float]]:
    cohorts: dict[int, Cohort] = {}
    selected_deciles: dict[int, dict[int, tuple[int, ...]]] = {}
    minima = {"min_weight_sum": 1.0, "max_weight_sum": 1.0, "min_decile_size": math.inf, "max_decile_size": 0}
    for formation, signals in signals_by_month.items():
        members, evidence = rank_deciles(signals)
        cohorts[formation] = Cohort(
            members[1], members[10], len(signals),
            tuple(len(members[d]) for d in range(1, 11)),
        )
        if formation in selected:
            selected_deciles[formation] = members
        minima["min_weight_sum"] = min(minima["min_weight_sum"], evidence["min_weight_sum"])
        minima["max_weight_sum"] = max(minima["max_weight_sum"], evidence["max_weight_sum"])
        minima["min_decile_size"] = min(minima["min_decile_size"], evidence["min_decile_size"])
        minima["max_decile_size"] = max(minima["max_decile_size"], evidence["max_decile_size"])
    return cohorts, selected_deciles, minima


def classify_unusable(
    permno: int,
    holding_month: int,
    observation: tuple[float | None, bool, str] | None,
    first_month: dict[int, int],
    last_month: dict[int, int],
    last_flag: dict[int, str],
) -> str | None:
    if observation is not None:
        value, usable, flag = observation
        if usable:
            return None
        return "missing_return" if value is None else f"incomplete_delisting_{flag}"
    if holding_month < first_month[permno]:
        return "before_security_history"
    if holding_month <= last_month[permno]:
        return "internal_month_gap"
    flag = last_flag[permno]
    if flag in {"A", "P", "V"}:
        return "after_complete_delisting"
    if flag in {"G", "M"}:
        return "after_incomplete_delisting"
    return "after_history_end_unresolved"


def cohort_leg_return(
    members: tuple[int, ...],
    month_returns: dict[int, tuple[float | None, bool, str]],
    holding_month: int,
    first_month: dict[int, int],
    last_month: dict[int, int],
    last_flag: dict[int, str],
    reason_slots: Counter[str],
    unique_reasons: set[tuple[int, int, str]],
) -> float:
    usable_values: list[float] = []
    for permno in members:
        observation = month_returns.get(permno)
        reason = classify_unusable(
            permno, holding_month, observation,
            first_month, last_month, last_flag,
        )
        if reason is not None:
            reason_slots[reason] += 1
            unique_reasons.add((permno, holding_month, reason))
            continue
        value = observation[0] if observation else None
        if value is None:
            raise RuntimeError("Usable holding return is missing")
        usable_values.append(float(value))
    if not usable_values:
        raise RuntimeError("No usable returns in a cohort leg")
    return statistics.fmean(usable_values)


def independent_full_recalculation(
    cohorts: dict[tuple[str, int], dict[int, Cohort]],
    by_stock: dict[int, dict[int, tuple[float | None, bool, str]]],
    by_month: dict[int, dict[int, tuple[float | None, bool, str]]],
    published_monthly: list[dict[str, str]],
) -> tuple[dict[tuple[str, int, int, int], tuple[float, float, float]], Counter[str], set[tuple[int, int, str]], dict[str, float]]:
    first_month = {permno: min(history) for permno, history in by_stock.items()}
    last_month = {permno: max(history) for permno, history in by_stock.items()}
    last_flag = {permno: history[last_month[permno]][2] for permno, history in by_stock.items()}
    published = {
        (row["panel"], int(row["J"]), int(row["K"]), month_index(int(row["yyyymm"]))): row
        for row in published_monthly
    }
    recalculated: dict[tuple[str, int, int, int], tuple[float, float, float]] = {}
    reasons: Counter[str] = Counter()
    unique_reasons: set[tuple[int, int, str]] = set()
    max_difference = 0.0
    min_cohorts = math.inf
    max_cohorts = 0
    invalid_age_count = 0
    for key, output in published.items():
        panel, j, k, holding = key
        formation_months = tuple(range(holding-k, holding))
        min_cohorts = min(min_cohorts, len(formation_months))
        max_cohorts = max(max_cohorts, len(formation_months))
        if any(not 1 <= holding-formation <= k for formation in formation_months):
            invalid_age_count += 1
        p1_cohorts: list[float] = []
        p10_cohorts: list[float] = []
        for formation in formation_months:
            cohort = cohorts[(panel, j)].get(formation)
            if cohort is None:
                raise RuntimeError(f"Missing cohort for {key}, formation {formation}")
            month_returns = by_month.get(holding, {})
            p1_cohorts.append(cohort_leg_return(
                cohort.p1, month_returns, holding, first_month, last_month,
                last_flag, reasons, unique_reasons,
            ))
            p10_cohorts.append(cohort_leg_return(
                cohort.p10, month_returns, holding, first_month, last_month,
                last_flag, reasons, unique_reasons,
            ))
        sell = statistics.fmean(p1_cohorts)
        buy = statistics.fmean(p10_cohorts)
        wml = buy-sell
        recalculated[key] = (sell, buy, wml)
        max_difference = max(
            max_difference,
            abs(sell-float(output["p1_return"])),
            abs(buy-float(output["p10_return"])),
            abs(wml-float(output["wml_return"])),
        )
    evidence = {
        "max_output_difference": max_difference,
        "min_active_cohorts": min_cohorts,
        "max_active_cohorts": max_cohorts,
        "invalid_age_count": invalid_age_count,
    }
    return recalculated, reasons, unique_reasons, evidence


def selected_raw_monthly_deciles(
    connection: duckdb.DuckDBPyConnection,
    raw_monthly: Path,
    eligibility: dict[int, list[int]],
) -> tuple[dict[int, dict[int, tuple[int, ...]]], dict[str, int]]:
    columns = {"permno": [], "formation": [], "required_yyyymm": []}
    selected_indices = [month_index(value) for value in SELECTED_FORMATIONS]
    for formation in selected_indices:
        for permno in eligibility[formation]:
            for required in range(formation-5, formation+1):
                columns["permno"].append(permno)
                columns["formation"].append(formation)
                columns["required_yyyymm"].append(yyyymm_from_index(required))
    requests = pa.table({
        "permno": pa.array(columns["permno"], pa.int64()),
        "formation": pa.array(columns["formation"], pa.int32()),
        "required_yyyymm": pa.array(columns["required_yyyymm"], pa.int32()),
    })
    connection.register("selected_month_requests", requests)
    raw = sql_path(raw_monthly)
    query = f"""
        SELECT q.permno, q.formation, q.required_yyyymm,
               count(r.PERMNO) AS source_rows,
               count(DISTINCT r.MthRet) AS return_values,
               count(DISTINCT r.MthDelFlg) AS flag_values,
               min(r.MthRet) AS mth_ret,
               min(r.MthDelFlg) AS mth_del_flg
        FROM selected_month_requests q
        LEFT JOIN read_csv_auto('{raw}', header=true, sample_size=100000) r
          ON q.permno=r.PERMNO AND q.required_yyyymm=CAST(r.YYYYMM AS INTEGER)
        GROUP BY q.permno, q.formation, q.required_yyyymm
        ORDER BY q.formation, q.permno, q.required_yyyymm
    """
    observations: dict[tuple[int, int], dict[int, tuple[float | None, str, int]]] = defaultdict(dict)
    conflicts = 0
    duplicate_excess = 0
    for permno, formation, required, source_rows, return_values, flag_values, value, flag in connection.execute(query).fetchall():
        conflicts += int(return_values > 1 or flag_values > 1)
        duplicate_excess += max(int(source_rows)-1, 0)
        observations[(int(formation), int(permno))][month_index(int(required))] = (
            value, flag or "", int(source_rows)
        )
    connection.unregister("selected_month_requests")
    if conflicts:
        raise RuntimeError("Raw monthly selected-date conflicts")
    result: dict[int, dict[int, tuple[int, ...]]] = {}
    ranked_counts: dict[int, int] = {}
    for formation in selected_indices:
        signals: list[tuple[int, float]] = []
        for permno in eligibility[formation]:
            history = observations[(formation, permno)]
            rows = [history.get(month) for month in range(formation-5, formation+1)]
            if any(row is None or row[2] == 0 for row in rows):
                continue
            if any(row[0] is None or row[0] < -1 or row[1] in {"G", "M"} for row in rows if row):
                continue
            signals.append((permno, compound([float(row[0]) for row in rows if row])))
        result[formation], _ = rank_deciles(signals)
        ranked_counts[formation] = len(signals)
    return result, {
        "duplicate_excess": duplicate_excess,
        **{f"ranked_{yyyymm_from_index(k)}": v for k, v in ranked_counts.items()},
    }


def raw_market_calendar(
    connection: duckdb.DuckDBPyConnection, raw_daily: Path
) -> list[date]:
    rows = connection.execute(
        f"SELECT DISTINCT DlyCalDt FROM read_csv_auto('{sql_path(raw_daily)}', "
        "header=true, sample_size=100000) ORDER BY DlyCalDt"
    ).fetchall()
    return [row[0] for row in rows]


def selected_raw_daily_deciles(
    connection: duckdb.DuckDBPyConnection,
    raw_daily: Path,
    eligibility: dict[int, list[int]],
    dates: list[date],
) -> tuple[dict[int, dict[int, tuple[int, ...]]], dict[int, tuple[date, date, date]], dict[str, int]]:
    columns = {"permno": [], "formation": [], "start_date": [], "end_date": []}
    boundaries: dict[int, tuple[date, date, date]] = {}
    for yyyymm in SELECTED_FORMATIONS:
        formation = month_index(yyyymm)
        formation_date = first_market_in_month(dates, formation+1)
        if formation_date is None:
            raise RuntimeError("Missing selected formation trading date")
        end_date = last_market_on_or_before(dates, formation_date-timedelta(days=7))
        start_date = last_market_on_or_before(dates, subtract_months(end_date, 6)) if end_date else None
        if end_date is None or start_date is None:
            raise RuntimeError("Missing selected Panel B boundary")
        boundaries[formation] = (formation_date, start_date, end_date)
        for permno in eligibility[formation]:
            columns["permno"].append(permno)
            columns["formation"].append(formation)
            columns["start_date"].append(start_date)
            columns["end_date"].append(end_date)
    candidates = pa.table({
        "permno": pa.array(columns["permno"], pa.int64()),
        "formation": pa.array(columns["formation"], pa.int32()),
        "start_date": pa.array(columns["start_date"], pa.date32()),
        "end_date": pa.array(columns["end_date"], pa.date32()),
    })
    connection.register("selected_daily_candidates", candidates)
    ranges = list(boundaries.values())
    date_filter = " OR ".join(
        f"(r.DlyCalDt BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}')"
        for _, start, end in ranges
    )
    raw = sql_path(raw_daily)
    query = f"""
        WITH universe AS (
            SELECT DISTINCT permno FROM selected_daily_candidates
        ), source AS (
            SELECT r.PERMNO AS permno, r.DlyCalDt AS trading_date,
                   r.DlyRet AS dly_ret
            FROM read_csv_auto('{raw}', header=true, sample_size=100000) r
            JOIN universe u ON r.PERMNO=u.permno
            WHERE {date_filter}
        ), daily AS (
            SELECT permno, trading_date, min(dly_ret) AS dly_ret,
                   count(*) AS source_rows,
                   count(DISTINCT dly_ret) AS return_values
            FROM source GROUP BY permno, trading_date
        )
        SELECT c.permno, c.formation, c.start_date, c.end_date,
               count(d.trading_date) FILTER (WHERE d.trading_date=c.start_date) AS start_coverage,
               count(d.trading_date) FILTER (WHERE d.trading_date=c.end_date) AS end_coverage,
               sum(CASE WHEN d.trading_date>c.start_date AND d.dly_ret IS NOT NULL
                        THEN ln(1.0+d.dly_ret) ELSE 0.0 END) AS log_return,
               count(d.dly_ret) FILTER (WHERE d.trading_date>c.start_date) AS valid_returns,
               max(d.return_values) AS max_return_values,
               sum(d.source_rows-1) AS duplicate_excess
        FROM selected_daily_candidates c
        LEFT JOIN daily d ON c.permno=d.permno
          AND d.trading_date BETWEEN c.start_date AND c.end_date
        GROUP BY c.permno, c.formation, c.start_date, c.end_date
        ORDER BY c.formation, c.permno
    """
    signals: dict[int, list[tuple[int, float]]] = defaultdict(list)
    conflicts = duplicate_excess = 0
    for row in connection.execute(query).fetchall():
        permno, formation, _, _, start_coverage, end_coverage, log_return, valid_returns, max_values, duplicates = row
        conflicts += int((max_values or 0) > 1)
        duplicate_excess += int(duplicates or 0)
        if start_coverage != 1 or end_coverage != 1 or valid_returns <= 0:
            continue
        signals[int(formation)].append((int(permno), math.exp(float(log_return))-1.0))
    connection.unregister("selected_daily_candidates")
    if conflicts:
        raise RuntimeError("Raw daily selected-date return conflicts")
    deciles: dict[int, dict[int, tuple[int, ...]]] = {}
    counts: dict[str, int] = {"duplicate_excess": duplicate_excess}
    for formation, values in signals.items():
        deciles[formation], _ = rank_deciles(values)
        counts[f"ranked_{yyyymm_from_index(formation)}"] = len(values)
    return deciles, boundaries, counts


def selected_raw_holding_returns(
    connection: duckdb.DuckDBPyConnection,
    raw_monthly: Path,
    selected_deciles: dict[tuple[str, int, int], dict[int, tuple[int, ...]]],
    published: dict[tuple[str, int, int, int], dict[str, str]],
) -> tuple[float, list[str]]:
    columns: dict[str, list] = {
        "panel": [], "holding": [], "formation": [], "leg": [], "permno": []
    }
    for holding_yyyymm in SELECTED_HOLDINGS:
        holding = month_index(holding_yyyymm)
        for panel in ("A", "B"):
            for formation in range(holding-6, holding):
                deciles = selected_deciles[(panel, 6, formation)]
                for leg, members in (("P1", deciles[1]), ("P10", deciles[10])):
                    for permno in members:
                        columns["panel"].append(panel)
                        columns["holding"].append(holding)
                        columns["formation"].append(formation)
                        columns["leg"].append(leg)
                        columns["permno"].append(permno)
    requests = pa.table({
        "panel": pa.array(columns["panel"], pa.string()),
        "holding": pa.array(columns["holding"], pa.int32()),
        "formation": pa.array(columns["formation"], pa.int32()),
        "leg": pa.array(columns["leg"], pa.string()),
        "permno": pa.array(columns["permno"], pa.int64()),
    })
    connection.register("selected_holding_requests", requests)
    raw = sql_path(raw_monthly)
    query = f"""
        SELECT q.panel, q.holding, q.formation, q.leg, q.permno,
               count(r.PERMNO) AS source_rows,
               count(DISTINCT r.MthRet) AS return_values,
               count(DISTINCT r.MthDelFlg) AS flag_values,
               min(r.MthRet) AS mth_ret, min(r.MthDelFlg) AS mth_del_flg
        FROM selected_holding_requests q
        LEFT JOIN read_csv_auto('{raw}', header=true, sample_size=100000) r
          ON q.permno=r.PERMNO
         AND CAST(r.YYYYMM AS INTEGER)=((q.holding//12)*100+(q.holding%12)+1)
        GROUP BY q.panel, q.holding, q.formation, q.leg, q.permno
    """
    values: dict[tuple[str, int, int, str], list[float]] = defaultdict(list)
    conflicts = 0
    for panel, holding, formation, leg, _, source_rows, return_values, flag_values, value, flag in connection.execute(query).fetchall():
        conflicts += int(return_values > 1 or flag_values > 1)
        if source_rows == 0 or value is None or value < -1 or (flag or "") in {"G", "M"}:
            continue
        values[(panel, int(holding), int(formation), leg)].append(float(value))
    connection.unregister("selected_holding_requests")
    if conflicts:
        raise RuntimeError("Selected holding returns contain raw conflicts")
    max_difference = 0.0
    evidence: list[str] = []
    for holding_yyyymm in SELECTED_HOLDINGS:
        holding = month_index(holding_yyyymm)
        for panel in ("A", "B"):
            p1 = statistics.fmean(
                statistics.fmean(values[(panel, holding, formation, "P1")])
                for formation in range(holding-6, holding)
            )
            p10 = statistics.fmean(
                statistics.fmean(values[(panel, holding, formation, "P10")])
                for formation in range(holding-6, holding)
            )
            wml = p10-p1
            output = published[(panel, 6, 6, holding)]
            difference = max(
                abs(p1-float(output["p1_return"])),
                abs(p10-float(output["p10_return"])),
                abs(wml-float(output["wml_return"])),
            )
            max_difference = max(max_difference, difference)
            evidence.append(
                f"{panel} {holding_yyyymm}: Sell={p1:.8f}, Buy={p10:.8f}, "
                f"WML={wml:.8f}, max diff={difference:.2e}"
            )
    return max_difference, evidence


def test_row(name: str, method: str, status: str, evidence: str, issue: str = "None") -> dict[str, str]:
    return {"name": name, "method": method, "status": status, "evidence": evidence, "issue": issue}


def markdown_table(rows: list[dict[str, str]]) -> str:
    lines = [
        "| Test | Method | Status | Numerical evidence | Issue / assumption |",
        "|---|---|---:|---|---|",
    ]
    for row in rows:
        values = [row[key].replace("|", "\\|").replace("\n", "<br>") for key in ("name", "method", "status", "evidence", "issue")]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=ROOT/"processed"/"monthly_stock_prepared.parquet")
    parser.add_argument("--monthly-lookup", type=Path, default=ROOT/"processed"/"table1_monthly_return_lookup.parquet")
    parser.add_argument("--daily-cumulative", type=Path, default=ROOT/"processed"/"table1_daily_cumulative.parquet")
    parser.add_argument("--raw-monthly", type=Path, default=ROOT/"02_Monthly_Stocks_and_Factors"/"data"/"monthly_stock.csv")
    parser.add_argument("--raw-daily", type=Path, default=ROOT/"03_Daily_Stocks"/"data"/"daily_stock.csv")
    parser.add_argument("--monthly-output", type=Path, default=ROOT/"outputs"/"tables"/"table1_monthly_returns.csv")
    parser.add_argument("--summary-output", type=Path, default=ROOT/"outputs"/"tables"/"table1_replication.csv")
    parser.add_argument("--report", type=Path, default=ROOT/"outputs"/"logs"/"table1_validation.md")
    args = parser.parse_args()
    existing_paths = (
        args.prepared, args.monthly_lookup, args.daily_cumulative,
        args.raw_monthly, args.raw_daily, args.monthly_output, args.summary_output,
    )
    for path in existing_paths:
        if not path.is_file():
            raise SystemExit(f"Required input not found: {path}")
    file_states_before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns) for path in existing_paths
    }

    tests: list[dict[str, str]] = []
    connection = duckdb.connect()
    connection.execute("SET memory_limit='4GB'")
    eligibility, by_stock, by_month, prepared_keys = read_validation_inputs(
        args.prepared, args.monthly_lookup
    )
    published_monthly = load_csv(args.monthly_output)
    published_summary = load_csv(args.summary_output)

    selected_set = {month_index(value) for value in SELECTED_FORMATIONS}
    for holding_yyyymm in SELECTED_HOLDINGS:
        holding = month_index(holding_yyyymm)
        selected_set.update(range(holding-6, holding))
    cohorts: dict[tuple[str, int], dict[int, Cohort]] = {}
    selected_deciles: dict[tuple[str, int, int], dict[int, tuple[int, ...]]] = {}
    weight_min, weight_max = 1.0, 1.0
    decile_min, decile_max = math.inf, 0
    for j in J_VALUES:
        signals = panel_a_signals(eligibility, by_stock, j)
        formed, selected, evidence = make_cohorts(signals, selected_set)
        cohorts[("A", j)] = formed
        for formation, deciles in selected.items():
            selected_deciles[("A", j, formation)] = deciles
        weight_min = min(weight_min, evidence["min_weight_sum"])
        weight_max = max(weight_max, evidence["max_weight_sum"])
        decile_min = min(decile_min, evidence["min_decile_size"])
        decile_max = max(decile_max, evidence["max_decile_size"])

    compact_dates = market_dates(connection, args.daily_cumulative)
    b_candidates, b_boundaries = build_panel_b_candidates(eligibility, compact_dates)
    b_signals = panel_b_signals(connection, b_candidates, args.daily_cumulative)
    for j in J_VALUES:
        formed, selected, evidence = make_cohorts(b_signals[j], selected_set)
        cohorts[("B", j)] = formed
        for formation, deciles in selected.items():
            selected_deciles[("B", j, formation)] = deciles
        weight_min = min(weight_min, evidence["min_weight_sum"])
        weight_max = max(weight_max, evidence["max_weight_sum"])
        decile_min = min(decile_min, evidence["min_decile_size"])
        decile_max = max(decile_max, evidence["max_decile_size"])

    raw_a_deciles, raw_a_evidence = selected_raw_monthly_deciles(
        connection, args.raw_monthly, eligibility
    )
    a_mismatches = 0
    a_evidence_lines: list[str] = []
    for yyyymm in SELECTED_FORMATIONS:
        formation = month_index(yyyymm)
        actual = selected_deciles[("A", 6, formation)]
        raw = raw_a_deciles[formation]
        mismatch = sum(set(actual[d]) != set(raw[d]) for d in range(1, 11))
        a_mismatches += mismatch
        a_evidence_lines.append(
            f"{yyyymm}: eligible={len(eligibility[formation])}, "
            f"ranked={raw_a_evidence[f'ranked_{yyyymm}']}, "
            f"P1/P10={len(raw[1])}/{len(raw[10])}, decile mismatches={mismatch}"
        )
    tests.append(test_row(
        "Raw-monthly J=6 formation replication",
        "Read the original monthly CSV for all eligible stocks on three deterministic formation dates; independently compound six returns and rank the full universe.",
        "PASS" if a_mismatches == 0 else "FAIL",
        "; ".join(a_evidence_lines) + f"; raw duplicate excess rows reconciled={raw_a_evidence['duplicate_excess']}",
        "None" if a_mismatches == 0 else "Full-universe decile membership differs.",
    ))

    raw_dates = raw_market_calendar(connection, args.raw_daily)
    raw_b_deciles, raw_b_boundaries, raw_b_evidence = selected_raw_daily_deciles(
        connection, args.raw_daily, eligibility, raw_dates
    )
    b_mismatches = boundary_mismatches = 0
    b_evidence_lines: list[str] = []
    gap_days: list[int] = []
    for yyyymm in SELECTED_FORMATIONS:
        formation = month_index(yyyymm)
        actual = selected_deciles[("B", 6, formation)]
        raw = raw_b_deciles[formation]
        mismatch = sum(set(actual[d]) != set(raw[d]) for d in range(1, 11))
        b_mismatches += mismatch
        boundary_mismatches += int(raw_b_boundaries[formation] != b_boundaries[(formation, 6)])
        formation_date, start_date, end_date = raw_b_boundaries[formation]
        gap = (formation_date-end_date).days
        gap_days.append(gap)
        panel_difference = len(set(raw[1]).symmetric_difference(raw_a_deciles[formation][1])) + len(
            set(raw[10]).symmetric_difference(raw_a_deciles[formation][10])
        )
        b_evidence_lines.append(
            f"{yyyymm}: {start_date}..{end_date}, formation={formation_date}, gap={gap} days, "
            f"ranked={raw_b_evidence[f'ranked_{yyyymm}']}, decile mismatches={mismatch}, "
            f"P1/P10 membership differences versus Panel A={panel_difference}"
        )
    tests.append(test_row(
        "Raw-daily Panel B replication",
        "Read the original 26 GB daily CSV for the full eligible universes on the selected dates; compound daily returns over the independently derived six-month windows.",
        "PASS" if b_mismatches == 0 and boundary_mismatches == 0 else "FAIL",
        "; ".join(b_evidence_lines),
        "Panel B uses seven calendar days because the paper does not define calendar versus trading week.",
    ))

    published_map = {
        (row["panel"], int(row["J"]), int(row["K"]), month_index(int(row["yyyymm"]))): row
        for row in published_monthly
    }
    raw_holding_difference, raw_holding_evidence = selected_raw_holding_returns(
        connection, args.raw_monthly, selected_deciles, published_map
    )
    tests.append(test_row(
        "Selected-date portfolio arithmetic",
        "For early, middle, and late J=6/K=6 months, independently obtain raw monthly holding returns for all members of all six cohorts; equal weight within cohort and across cohorts.",
        "PASS" if raw_holding_difference <= TOLERANCE else "FAIL",
        "; ".join(raw_holding_evidence),
        "Unavailable returns are omitted only to reproduce the documented existing decision.",
    ))

    recalculated, missing_reasons, unique_missing, full_evidence = independent_full_recalculation(
        cohorts, by_stock, by_month, published_monthly
    )
    tests.append(test_row(
        "Full monthly return reconstruction",
        "Independently reconstruct all 9,216 strategy-month observations from rebuilt cohorts and compare Sell, Buy, and WML with the published monthly CSV.",
        "PASS" if full_evidence["max_output_difference"] <= TOLERANCE else "FAIL",
        f"rows={len(recalculated):,}; maximum absolute difference={full_evidence['max_output_difference']:.3e}; "
        f"active cohort range={int(full_evidence['min_active_cohorts'])}-{int(full_evidence['max_active_cohorts'])}; "
        f"invalid cohort ages={int(full_evidence['invalid_age_count'])}",
        "None",
    ))

    wml_identity_max = max(
        abs(float(row["wml_return"])-float(row["p10_return"])+float(row["p1_return"]))
        for row in published_monthly
    )
    duplicate_output_keys = len(published_monthly)-len(published_map)
    invalid_active = sum(int(row["active_cohorts"]) != int(row["K"]) for row in published_monthly)
    tests.append(test_row(
        "Output invariants and weights",
        "Check every published strategy-month key, WML identity, active-cohort count, independently rebuilt decile uniqueness/order/size, and equal-weight sums.",
        "PASS" if duplicate_output_keys == 0 and invalid_active == 0 and wml_identity_max <= TOLERANCE and abs(weight_min-1) <= TOLERANCE and abs(weight_max-1) <= TOLERANCE else "FAIL",
        f"duplicate output keys={duplicate_output_keys}; WML max error={wml_identity_max:.3e}; invalid active-cohort rows={invalid_active}; "
        f"decile sizes={int(decile_min)}-{int(decile_max)}; weight-sum range={weight_min:.16f}-{weight_max:.16f}",
        "Exact signal ties use ascending PERMNO, matching the documented deterministic assumption.",
    ))

    summary_map = {
        (row["panel"], int(row["formation_months_J"]), int(row["holding_months_K"]), row["portfolio"]): row
        for row in published_summary
    }
    grouped: dict[tuple[str, int, int], list[tuple[int, tuple[float, float, float]]]] = defaultdict(list)
    for (panel, j, k, holding), values in recalculated.items():
        grouped[(panel, j, k)].append((holding, values))
    max_mean_diff = max_se_diff = max_t_diff = 0.0
    for (panel, j, k), observations in grouped.items():
        observations.sort()
        for portfolio, position in (("Sell", 0), ("Buy", 1), ("Buy-Sell", 2)):
            values = [returns[position] for _, returns in observations]
            mean = statistics.fmean(values)
            se = statistics.stdev(values)/math.sqrt(len(values))
            t_stat = mean/se
            output = summary_map[(panel, j, k, portfolio)]
            max_mean_diff = max(max_mean_diff, abs(mean-float(output["modern_mean_monthly_return"])))
            max_se_diff = max(max_se_diff, abs(se-float(output["modern_standard_error"])))
            max_t_diff = max(max_t_diff, abs(t_stat-float(output["modern_t_statistic"])))
    tests.append(test_row(
        "Summary statistics",
        "Use Python statistics.fmean/stdev on the independently rebuilt monthly series; compute SE=s/√T and t=mean/SE for all 96 cells.",
        "PASS" if max_mean_diff <= 5e-10 and max_se_diff <= 5e-10 and max_t_diff <= 5e-4 else "FAIL",
        f"summary rows={len(summary_map)}; max mean diff={max_mean_diff:.3e}; max SE diff={max_se_diff:.3e}; max t diff={max_t_diff:.3e}",
        "Conventional t-statistics do not correct serial dependence from overlapping portfolios.",
    ))

    output_months = sorted({month_index(int(row["yyyymm"])) for row in published_monthly})
    december_2001 = month_index(200112)
    january_2002 = month_index(200201)
    previous_valid = all(
        all(formation in cohorts[(panel, j)] for formation in range(december_2001-k, december_2001))
        for panel in ("A", "B") for j in J_VALUES for k in K_VALUES
    )
    first_valid = all(
        all(formation in cohorts[(panel, j)] for formation in range(january_2002-k, january_2002))
        for panel in ("A", "B") for j in J_VALUES for k in K_VALUES
    )
    tests.append(test_row(
        "Common evaluation period",
        "Check the published calendar and test whether all 32 strategies possess every required cohort in December 2001 and January 2002.",
        "PASS" if len(output_months) == 288 and yyyymm_from_index(output_months[0]) == 200201 and yyyymm_from_index(output_months[-1]) == 202512 and not previous_valid and first_valid else "FAIL",
        f"months={len(output_months)}; range={yyyymm_from_index(output_months[0])}-{yyyymm_from_index(output_months[-1])}; "
        f"all strategies valid in 200112={previous_valid}; in 200201={first_valid}",
        "January 2002 is the first month with the extra Panel B week plus 12-month formation and 12 overlapping holding cohorts available from data beginning in January 2000.",
    ))

    total_unusable = sum(missing_reasons.values())
    expected_slots = sum(
        int(row["p1_expected_slots"])+int(row["p10_expected_slots"])
        for row in published_monthly
    )
    reason_text = ", ".join(f"{key}={value:,}" for key, value in sorted(missing_reasons.items()))
    tests.append(test_row(
        "Missing returns and delisting treatment",
        "Classify every unusable component slot by raw-month history position and final delisting flag; confirm that the existing calculation omits it and renormalizes the remaining cohort members.",
        "WARNING",
        f"unusable={total_unusable:,}/{expected_slots:,} ({total_unusable/expected_slots:.2%}); unique stock-month-reasons={len(unique_missing):,}; {reason_text}",
        "Renormalizing available securities is documented and never inserts zero, but missingness/delisting is nonrandom and can change returns. The paper and supplied documentation do not fully specify an alternative for incomplete G/M returns.",
    ))

    full_gap_by_formation = {
        formation: (values[0]-values[2]).days
        for (formation, j), values in b_boundaries.items() if j == 6
    }
    full_gap_counts = Counter(full_gap_by_formation.values())
    tests.append(test_row(
        "Panel B one-week convention",
        "Compare first holding trading dates with raw-daily signal endpoints on selected dates and confirm no daily return after the endpoint enters the signal.",
        "WARNING",
        f"full-sample realized calendar gaps={min(full_gap_counts)}-{max(full_gap_counts)} days "
        f"({', '.join(f'{gap}d={count}' for gap, count in sorted(full_gap_counts.items()))}); "
        f"selected gaps={gap_days}; boundary mismatches={boundary_mismatches}; raw daily decile mismatches={b_mismatches}",
        "The seven-calendar-day rule is internally correct and uses daily data, but the paper does not state whether 'one week' means seven calendar days or five trading days.",
    ))

    tests.append(test_row(
        "Look-ahead and date alignment",
        "For every reconstructed output, require formation months h-K..h-1; Panel A signals end in formation month t and Panel B daily signals end before the first trading day of t+1.",
        "PASS" if full_evidence["invalid_age_count"] == 0 and boundary_mismatches == 0 else "FAIL",
        f"invalid cohort ages={int(full_evidence['invalid_age_count'])}; Panel B boundary mismatches={boundary_mismatches}; selected gaps={gap_days}",
        "Eligibility is observed in month t; no holding-month return or future availability determines formation eligibility.",
    ))

    file_states_after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns) for path in existing_paths
    }
    changed_files = [str(path) for path in existing_paths if file_states_before[path] != file_states_after[path]]
    tests.append(test_row(
        "Read-only input/result integrity",
        "Compare byte size and nanosecond modification time for every raw, processed, and existing Table I result file before and after validation.",
        "PASS" if not changed_files else "FAIL",
        f"files checked={len(existing_paths)}; changed files={len(changed_files)}",
        "None" if not changed_files else "; ".join(changed_files),
    ))

    failures = [row for row in tests if row["status"] == "FAIL"]
    warnings = [row for row in tests if row["status"] == "WARNING"]
    overall = "FAIL" if failures else ("CONDITIONALLY VALIDATED" if warnings else "PASS")
    conclusion = (
        "The numerical implementation is independently reproduced and all required invariants pass. "
        "Table I is computationally validated, but should be described as methodologically conditional "
        "until the seven-calendar-day Panel B convention and the renormalization treatment for unavailable "
        "holding returns are approved or accompanied by robustness checks."
        if not failures else
        "At least one required invariant failed. Do not treat Table I as validated until the listed failures are corrected and reviewed."
    )
    report = f"""# Independent validation of Table I

**Overall status: {overall}**

This script is independent of `src/04_table1_replication.py`: it does not import
or call any original portfolio-construction function. Existing raw, processed,
and result files were opened read-only. The only generated artifact is this
report.

Deterministic full-universe formation dates: {', '.join(map(str, SELECTED_FORMATIONS))}.  
Deterministic J=6/K=6 holding months: {', '.join(map(str, SELECTED_HOLDINGS))}.

## Test results

{markdown_table(tests)}

## Assessment

{conclusion}

No research result or existing Table I file was changed by this validation.
"""
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    args.report.chmod(0o644)
    print(report)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
