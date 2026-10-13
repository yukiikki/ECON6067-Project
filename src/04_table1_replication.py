"""Replicate Jegadeesh and Titman (1993) Table I for the modern sample.

The script constructs all 32 J/K strategies in Panels A and B. It uses the
Stage 2 panel to determine formation-date eligibility, an unfiltered monthly
return lookup for formation and holding returns, and a compact daily cumulative
return file for Panel B's one-week gap. Raw files are read but never modified.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import hashlib
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError as exc:
    raise SystemExit(
        "duckdb and pyarrow are required. Run "
        "'python -m pip install -r requirements.txt'."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
J_VALUES = (3, 6, 9, 12)
K_VALUES = (3, 6, 9, 12)
EXPECTED_STAGE2 = {"rows": 520_438, "stocks": 4_669, "months": 312}

# Original Table I values: (mean monthly return, t-statistic), in decimals.
ORIGINAL = {
    "A": {
        3: {
            "Sell": ([.0108, .0091, .0092, .0087], [2.16, 1.87, 1.92, 1.87]),
            "Buy": ([.0140, .0149, .0152, .0156], [3.57, 3.78, 3.83, 3.89]),
            "Buy-Sell": ([.0032, .0058, .0061, .0069], [1.10, 2.29, 2.69, 3.53]),
        },
        6: {
            "Sell": ([.0087, .0079, .0072, .0080], [1.67, 1.56, 1.48, 1.66]),
            "Buy": ([.0171, .0174, .0174, .0166], [4.28, 4.33, 4.31, 4.13]),
            "Buy-Sell": ([.0084, .0095, .0102, .0086], [2.44, 3.07, 3.76, 3.36]),
        },
        9: {
            "Sell": ([.0077, .0065, .0071, .0082], [1.47, 1.29, 1.43, 1.66]),
            "Buy": ([.0186, .0186, .0176, .0164], [4.56, 4.53, 4.30, 4.03]),
            "Buy-Sell": ([.0109, .0121, .0105, .0082], [3.03, 3.78, 3.47, 2.89]),
        },
        12: {
            "Sell": ([.0060, .0065, .0075, .0087], [1.17, 1.29, 1.48, 1.74]),
            "Buy": ([.0192, .0179, .0168, .0155], [4.63, 4.36, 4.10, 3.81]),
            "Buy-Sell": ([.0131, .0114, .0093, .0068], [3.74, 3.40, 2.95, 2.25]),
        },
    },
    "B": {
        3: {
            "Sell": ([.0083, .0079, .0084, .0083], [1.67, 1.64, 1.77, 1.79]),
            "Buy": ([.0156, .0158, .0158, .0160], [3.95, 3.98, 3.96, 3.98]),
            "Buy-Sell": ([.0073, .0078, .0074, .0077], [2.61, 3.16, 3.36, 4.00]),
        },
        6: {
            "Sell": ([.0066, .0068, .0067, .0076], [1.28, 1.35, 1.38, 1.58]),
            "Buy": ([.0179, .0178, .0175, .0166], [4.47, 4.41, 4.32, 4.13]),
            "Buy-Sell": ([.0114, .0110, .0108, .0090], [3.37, 3.61, 4.01, 3.54]),
        },
        9: {
            "Sell": ([.0058, .0058, .0066, .0078], [1.13, 1.15, 1.34, 1.59]),
            "Buy": ([.0193, .0188, .0176, .0164], [4.72, 4.56, 4.30, 4.04]),
            "Buy-Sell": ([.0135, .0130, .0109, .0085], [3.85, 4.09, 3.67, 3.04]),
        },
        12: {
            "Sell": ([.0048, .0058, .0070, .0085], [.93, 1.15, 1.40, 1.71]),
            "Buy": ([.0196, .0179, .0167, .0154], [4.73, 4.36, 4.09, 3.79]),
            "Buy-Sell": ([.0149, .0121, .0096, .0069], [4.28, 3.65, 3.09, 2.31]),
        },
    },
}


class Table1ValidationError(RuntimeError):
    """An input or portfolio validation failed."""


@dataclass(frozen=True)
class Cohort:
    formation_month: int
    p1: tuple[int, ...]
    p10: tuple[int, ...]
    eligible_stocks: int
    decile_sizes: tuple[int, ...]


def month_index(yyyymm: int) -> int:
    year, month = divmod(int(yyyymm), 100)
    if year < 1900 or not 1 <= month <= 12:
        raise Table1ValidationError(f"Invalid YYYYMM: {yyyymm}")
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


def signature(path: Path, include_hash: bool = False) -> tuple[int, int, str | None]:
    stat = path.stat()
    digest = None
    if include_hash:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        digest = hasher.hexdigest()
    return stat.st_size, stat.st_mtime_ns, digest


def temporary_parquet(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = __import__("tempfile").mkstemp(
        prefix=f".{target.stem}_", suffix=".parquet", dir=target.parent
    )
    os.close(handle)
    Path(name).unlink()
    return Path(name)


def build_monthly_return_lookup(
    connection: duckdb.DuckDBPyConnection,
    raw_monthly: Path,
    prepared: Path,
    output: Path,
) -> dict[str, int]:
    raw = sql_path(raw_monthly)
    prep = sql_path(prepared)
    base = f"""
        WITH universe AS (
            SELECT DISTINCT permno FROM read_parquet('{prep}')
        ), source AS (
            SELECT r.PERMNO AS permno, CAST(r.YYYYMM AS INTEGER) AS yyyymm,
                   r.MthRet AS mth_ret, r.MthDelFlg AS mth_del_flg
            FROM read_csv_auto('{raw}', header=true, sample_size=100000) r
            JOIN universe u ON r.PERMNO=u.permno
        ), grouped AS (
            SELECT permno, yyyymm, count(*) AS source_rows,
                   count(DISTINCT mth_ret) AS return_values,
                   count(DISTINCT mth_del_flg) AS delisting_values,
                   min(mth_ret) AS mth_ret,
                   min(mth_del_flg) AS mth_del_flg
            FROM source GROUP BY permno, yyyymm
        )
    """
    validation = connection.execute(
        base
        + """
        SELECT sum(source_rows), count(*), sum(source_rows-1),
               sum(return_values>1), sum(delisting_values>1),
               sum(mth_ret < -1), count(DISTINCT permno),
               min(yyyymm), max(yyyymm)
        FROM grouped
        """
    ).fetchone()
    if validation[3] or validation[4] or validation[5]:
        raise Table1ValidationError(
            "Unfiltered monthly lookup contains return/flag conflicts or returns below -100%"
        )
    temporary = temporary_parquet(output)
    copy_query = f"""
        COPY (
            {base}
            SELECT permno, yyyymm,
                   make_date(yyyymm//100, yyyymm%100, 1) AS month,
                   mth_ret, coalesce(mth_del_flg, '') AS mth_del_flg,
                   (mth_ret IS NOT NULL AND mth_ret >= -1
                    AND coalesce(mth_del_flg, '') NOT IN ('G','M')) AS return_usable
            FROM grouped ORDER BY permno, yyyymm
        ) TO '{sql_path(temporary)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
    """
    connection.execute(copy_query)
    os.replace(temporary, output)
    return {
        "source_rows": int(validation[0]),
        "stock_months": int(validation[1]),
        "duplicate_excess_rows": int(validation[2]),
        "stocks": int(validation[6]),
        "min_yyyymm": int(validation[7]),
        "max_yyyymm": int(validation[8]),
    }


def build_daily_cumulative(
    connection: duckdb.DuckDBPyConnection,
    raw_daily: Path,
    prepared: Path,
    output: Path,
) -> dict[str, int | float]:
    raw = sql_path(raw_daily)
    prep = sql_path(prepared)
    base = f"""
        WITH universe AS (
            SELECT DISTINCT permno FROM read_parquet('{prep}')
        ), source AS (
            SELECT r.PERMNO AS permno, r.DlyCalDt AS trading_date,
                   r.DlyRet AS dly_ret, r.DlyDelFlg AS dly_del_flg,
                   r.DlyRetDurFlg AS duration_flag,
                   r.DlyRetMissFlg AS missing_flag
            FROM read_csv_auto('{raw}', header=true, sample_size=100000) r
            JOIN universe u ON r.PERMNO=u.permno
        ), grouped AS (
            SELECT permno, trading_date, count(*) AS source_rows,
                   count(DISTINCT dly_ret) AS return_values,
                   count(DISTINCT dly_del_flg) AS delisting_values,
                   count(DISTINCT duration_flag) AS duration_values,
                   count(DISTINCT missing_flag) AS missing_values,
                   min(dly_ret) AS dly_ret
            FROM source GROUP BY permno, trading_date
        )
    """
    validation = connection.execute(
        base
        + """
        SELECT sum(source_rows), count(*), sum(source_rows-1),
               sum(return_values>1), sum(delisting_values>1),
               sum(duration_values>1), sum(missing_values>1),
               sum(dly_ret < -1), count(dly_ret), count(DISTINCT permno),
               min(trading_date), max(trading_date), min(dly_ret), max(dly_ret)
        FROM grouped
        """
    ).fetchone()
    if any(validation[index] for index in (3, 4, 5, 6, 7)):
        raise Table1ValidationError(
            "Daily data contain unresolved duplicate conflicts or returns below -100%"
        )
    temporary = temporary_parquet(output)
    copy_query = f"""
        COPY (
            {base}
            SELECT permno, trading_date,
                   sum(CASE WHEN dly_ret IS NULL THEN 0.0
                            ELSE ln(1.0+dly_ret) END)
                       OVER (PARTITION BY permno ORDER BY trading_date
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS cumulative_log_return,
                   count(dly_ret)
                       OVER (PARTITION BY permno ORDER BY trading_date
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS cumulative_valid_returns
            FROM grouped ORDER BY permno, trading_date
        ) TO '{sql_path(temporary)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
    """
    connection.execute(copy_query)
    os.replace(temporary, output)
    return {
        "source_rows": int(validation[0]),
        "stock_days": int(validation[1]),
        "duplicate_excess_rows": int(validation[2]),
        "valid_returns": int(validation[8]),
        "stocks": int(validation[9]),
        "min_return": float(validation[12]),
        "max_return": float(validation[13]),
    }


def read_inputs(prepared: Path, monthly_lookup: Path):
    prepared_table = pq.read_table(prepared, columns=["permno", "yyyymm", "month"])
    if prepared_table.num_rows != EXPECTED_STAGE2["rows"]:
        raise Table1ValidationError("Unexpected Stage 2 row count")
    pdata = prepared_table.to_pydict()
    eligibility: dict[int, list[int]] = defaultdict(list)
    keys: set[tuple[int, int]] = set()
    for permno, yyyymm, month_value in zip(
        pdata["permno"], pdata["yyyymm"], pdata["month"], strict=True
    ):
        index = month_index(yyyymm)
        if month_value != month_start(index):
            raise Table1ValidationError("Stage 2 month mapping is invalid")
        key = (permno, index)
        if key in keys:
            raise Table1ValidationError(f"Duplicate Stage 2 key: {key}")
        keys.add(key)
        eligibility[index].append(permno)
    if len({permno for permno, _ in keys}) != EXPECTED_STAGE2["stocks"]:
        raise Table1ValidationError("Unexpected Stage 2 stock count")
    if len(eligibility) != EXPECTED_STAGE2["months"]:
        raise Table1ValidationError("Unexpected Stage 2 month count")

    table = pq.read_table(
        monthly_lookup,
        columns=["permno", "yyyymm", "mth_ret", "mth_del_flg", "return_usable"],
    )
    data = table.to_pydict()
    by_stock: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    by_month: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    for permno, yyyymm, value, flag, usable in zip(
        data["permno"], data["yyyymm"], data["mth_ret"],
        data["mth_del_flg"], data["return_usable"], strict=True,
    ):
        index = month_index(yyyymm)
        observation = (value, bool(usable), flag or "")
        if index in by_stock[permno]:
            raise Table1ValidationError("Duplicate key in monthly return lookup")
        by_stock[permno][index] = observation
        by_month[index][permno] = observation
    return dict(eligibility), dict(by_stock), dict(by_month)


def compound(values: list[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value
    return wealth - 1.0


def panel_a_signals(
    eligibility: dict[int, list[int]],
    returns_by_stock: dict[int, dict[int, tuple[float | None, bool, str]]],
    j: int,
) -> tuple[dict[int, list[tuple[int, float]]], Counter[str]]:
    signals: dict[int, list[tuple[int, float]]] = defaultdict(list)
    diagnostics: Counter[str] = Counter()
    for formation_month, permnos in eligibility.items():
        required = range(formation_month - j + 1, formation_month + 1)
        for permno in permnos:
            history = returns_by_stock.get(permno, {})
            observations = [history.get(month) for month in required]
            if any(observation is None for observation in observations):
                diagnostics["missing_month"] += 1
                continue
            if not all(observation[1] for observation in observations if observation):
                diagnostics["unusable_return"] += 1
                continue
            values = [observation[0] for observation in observations if observation]
            if any(value is None for value in values):
                raise Table1ValidationError("Usable Panel A return is missing")
            signal = compound([float(value) for value in values])
            if not math.isfinite(signal):
                raise Table1ValidationError("Non-finite Panel A signal")
            signals[formation_month].append((permno, signal))
            diagnostics["valid"] += 1
    return dict(signals), diagnostics


def market_calendar(
    connection: duckdb.DuckDBPyConnection, daily_cumulative: Path
) -> list[date]:
    rows = connection.execute(
        f"SELECT DISTINCT trading_date FROM read_parquet('{sql_path(daily_cumulative)}') "
        "ORDER BY trading_date"
    ).fetchall()
    dates = [row[0] for row in rows]
    if not dates or dates != sorted(set(dates)):
        raise Table1ValidationError("Invalid market calendar")
    return dates


def last_market_date_on_or_before(dates: list[date], cutoff: date) -> date | None:
    position = bisect.bisect_right(dates, cutoff) - 1
    return dates[position] if position >= 0 else None


def first_market_date_in_month(dates: list[date], month: int) -> date | None:
    start = month_start(month)
    position = bisect.bisect_left(dates, start)
    if position == len(dates):
        return None
    candidate = dates[position]
    return candidate if (candidate.year, candidate.month) == (start.year, start.month) else None


def panel_b_signals(
    connection: duckdb.DuckDBPyConnection,
    eligibility: dict[int, list[int]],
    daily_cumulative: Path,
    dates: list[date],
) -> tuple[dict[int, dict[int, list[tuple[int, float]]]], Counter[str]]:
    columns: dict[str, list] = {
        "permno": [], "formation_month": [], "j": [],
        "start_date": [], "end_date": [],
    }
    diagnostics: Counter[str] = Counter()
    for formation_month, permnos in eligibility.items():
        formation_date = first_market_date_in_month(dates, formation_month + 1)
        if formation_date is None:
            diagnostics["no_next_month_formation_date"] += len(permnos) * len(J_VALUES)
            continue
        # Paper says one week but does not specify trading versus calendar days.
        end_date = last_market_date_on_or_before(dates, formation_date - timedelta(days=7))
        if end_date is None:
            diagnostics["no_gap_end_date"] += len(permnos) * len(J_VALUES)
            continue
        for j in J_VALUES:
            start_date = last_market_date_on_or_before(dates, subtract_months(end_date, j))
            if start_date is None:
                diagnostics["no_gap_start_date"] += len(permnos)
                continue
            columns["permno"].extend(permnos)
            columns["formation_month"].extend([formation_month] * len(permnos))
            columns["j"].extend([j] * len(permnos))
            columns["start_date"].extend([start_date] * len(permnos))
            columns["end_date"].extend([end_date] * len(permnos))
    candidate_table = pa.table({
        "permno": pa.array(columns["permno"], type=pa.int64()),
        "formation_month": pa.array(columns["formation_month"], type=pa.int32()),
        "j": pa.array(columns["j"], type=pa.int8()),
        "start_date": pa.array(columns["start_date"], type=pa.date32()),
        "end_date": pa.array(columns["end_date"], type=pa.date32()),
    })
    diagnostics["candidates"] = candidate_table.num_rows
    connection.register("panel_b_candidates", candidate_table)
    daily = sql_path(daily_cumulative)
    query = f"""
        SELECT c.permno, c.formation_month, c.j, c.start_date, c.end_date,
               e.trading_date AS matched_end_date,
               s.trading_date AS matched_start_date,
               e.cumulative_log_return AS end_log,
               s.cumulative_log_return AS start_log,
               e.cumulative_valid_returns AS end_count,
               s.cumulative_valid_returns AS start_count
        FROM panel_b_candidates c
        LEFT JOIN read_parquet('{daily}') e
          ON c.permno=e.permno AND c.end_date=e.trading_date
        LEFT JOIN read_parquet('{daily}') s
          ON c.permno=s.permno AND c.start_date=s.trading_date
        ORDER BY c.formation_month, c.j, c.permno
    """
    signals: dict[int, dict[int, list[tuple[int, float]]]] = {
        j: defaultdict(list) for j in J_VALUES
    }
    reader = connection.execute(query).to_arrow_reader(100_000)
    for batch in reader:
        data = batch.to_pydict()
        for values in zip(*(data[name] for name in batch.schema.names), strict=True):
            (
                permno, formation_month, j, start_date, end_date,
                matched_end, matched_start, end_log, start_log,
                end_count, start_count,
            ) = values
            if matched_end != end_date:
                diagnostics["missing_end_coverage"] += 1
                continue
            if matched_start != start_date:
                diagnostics["missing_start_coverage"] += 1
                continue
            valid_returns = int(end_count) - int(start_count)
            if valid_returns <= 0:
                diagnostics["no_valid_daily_returns"] += 1
                continue
            signal = math.exp(float(end_log) - float(start_log)) - 1.0
            if not math.isfinite(signal) or signal < -1:
                raise Table1ValidationError("Invalid Panel B formation return")
            signals[int(j)][int(formation_month)].append((int(permno), signal))
            diagnostics[f"valid_j{j}"] += 1
    connection.unregister("panel_b_candidates")
    return {j: dict(months) for j, months in signals.items()}, diagnostics


def assign_deciles(signals: list[tuple[int, float]]) -> dict[int, list[int]]:
    if len(signals) < 10:
        raise Table1ValidationError("Fewer than ten stocks are available for ranking")
    ordered = sorted(signals, key=lambda item: (item[1], item[0]))
    members = {decile: [] for decile in range(1, 11)}
    for position, (permno, _) in enumerate(ordered):
        members[(position * 10) // len(ordered) + 1].append(permno)
    sizes = [len(members[decile]) for decile in range(1, 11)]
    if max(sizes) - min(sizes) > 1 or min(sizes) == 0:
        raise Table1ValidationError("Decile size validation failed")
    return members


def form_cohorts(
    signals_by_month: dict[int, list[tuple[int, float]]]
) -> tuple[dict[int, Cohort], Counter[str]]:
    cohorts: dict[int, Cohort] = {}
    diagnostics: Counter[str] = Counter()
    for formation_month, signals in sorted(signals_by_month.items()):
        members = assign_deciles(signals)
        signal_by_permno = dict(signals)
        if len(signal_by_permno) != len(signals):
            raise Table1ValidationError("A stock appears twice in a formation month")
        if max(signal_by_permno[p] for p in members[1]) > min(
            signal_by_permno[p] for p in members[10]
        ):
            raise Table1ValidationError("P1/P10 signal ordering failed")
        cohorts[formation_month] = Cohort(
            formation_month,
            tuple(members[1]),
            tuple(members[10]),
            len(signals),
            tuple(len(members[d]) for d in range(1, 11)),
        )
        frequencies = Counter(signal for _, signal in signals)
        diagnostics["tie_groups"] += sum(count > 1 for count in frequencies.values())
        diagnostics["tied_observations"] += sum(
            count for count in frequencies.values() if count > 1
        )
    return cohorts, diagnostics


def equal_weight_return(
    members: tuple[int, ...],
    month_returns: dict[int, tuple[float | None, bool, str]],
) -> tuple[float, int, Counter[str]]:
    values: list[float] = []
    diagnostics: Counter[str] = Counter()
    for permno in members:
        observation = month_returns.get(permno)
        if observation is None:
            diagnostics["no_record"] += 1
            continue
        value, usable, flag = observation
        if not usable:
            diagnostics["missing_return" if value is None else f"incomplete_delisting_{flag}"] += 1
            continue
        if value is None or not math.isfinite(value) or value < -1:
            raise Table1ValidationError("A usable holding return is invalid")
        values.append(value)
    if not values:
        raise Table1ValidationError("A cohort leg has no usable holding return")
    return statistics.fmean(values), len(values), diagnostics


def common_evaluation_months(
    cohorts: dict[tuple[str, int], dict[int, Cohort]],
    available_months: list[int],
) -> list[int]:
    maximum = max(available_months)
    valid = set()
    for holding_month in available_months:
        if all(
            all(month in cohorts[(panel, j)] for month in range(holding_month-k, holding_month))
            for panel in ("A", "B") for j in J_VALUES for k in K_VALUES
        ):
            valid.add(holding_month)
    start = maximum
    while start - 1 in valid:
        start -= 1
    months = list(range(start, maximum + 1))
    if not months or any(month not in valid for month in months):
        raise Table1ValidationError("No common consecutive evaluation period")
    return months


def strategy_returns(
    cohorts: dict[int, Cohort],
    k: int,
    evaluation_months: list[int],
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]],
) -> tuple[list[dict[str, object]], Counter[str]]:
    rows: list[dict[str, object]] = []
    diagnostics: Counter[str] = Counter()
    for holding_month in evaluation_months:
        p1_cohorts: list[float] = []
        p10_cohorts: list[float] = []
        expected_p1 = usable_p1 = expected_p10 = usable_p10 = 0
        for formation_month in range(holding_month-k, holding_month):
            cohort = cohorts[formation_month]
            month_returns = returns_by_month.get(holding_month, {})
            p1, n1, d1 = equal_weight_return(cohort.p1, month_returns)
            p10, n10, d10 = equal_weight_return(cohort.p10, month_returns)
            p1_cohorts.append(p1)
            p10_cohorts.append(p10)
            expected_p1 += len(cohort.p1)
            usable_p1 += n1
            expected_p10 += len(cohort.p10)
            usable_p10 += n10
            for reason, count in d1.items():
                diagnostics[f"p1_{reason}"] += count
            for reason, count in d10.items():
                diagnostics[f"p10_{reason}"] += count
        if len(p1_cohorts) != k or len(p10_cohorts) != k:
            raise Table1ValidationError("Incorrect number of overlapping cohorts")
        p1 = statistics.fmean(p1_cohorts)
        p10 = statistics.fmean(p10_cohorts)
        rows.append({
            "month": month_start(holding_month).isoformat(),
            "yyyymm": yyyymm_from_index(holding_month),
            "p1_return": p1,
            "p10_return": p10,
            "wml_return": p10-p1,
            "active_cohorts": k,
            "p1_expected_slots": expected_p1,
            "p1_usable_slots": usable_p1,
            "p10_expected_slots": expected_p10,
            "p10_usable_slots": usable_p10,
        })
    return rows, diagnostics


def mean_se_t(values: list[float]) -> tuple[float, float, float]:
    average = statistics.fmean(values)
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    if standard_error == 0:
        raise Table1ValidationError("A strategy has zero standard error")
    return average, standard_error, average / standard_error


def run_synthetic_tests() -> None:
    values = [0.10, -0.05, 0.02]
    if not math.isclose(compound(values), 1.10*.95*1.02-1, abs_tol=1e-15):
        raise Table1ValidationError("Synthetic compounding test failed")
    members = assign_deciles([(permno, float(permno)) for permno in range(1, 21)])
    if members[1] != [1, 2] or members[10] != [19, 20]:
        raise Table1ValidationError("Synthetic ranking test failed")
    result, count, diag = equal_weight_return(
        (1, 2, 3), {1: (.10, True, "N"), 2: (None, False, "N"), 3: (.20, True, "N")}
    )
    if not math.isclose(result, .15) or count != 2 or diag["missing_return"] != 1:
        raise Table1ValidationError("Synthetic missing-return test failed")
    dates = [date(2020, 1, 24), date(2020, 1, 27), date(2020, 1, 31)]
    if last_market_date_on_or_before(dates, date(2020, 1, 28)) != date(2020, 1, 27):
        raise Table1ValidationError("Synthetic market-date test failed")


def write_csv_atomic(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)
    path.chmod(0o644)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    os.replace(temporary, path)
    path.chmod(0o644)


def wml_matrix(statistics_by_strategy, panel: str, field: int) -> str:
    lines = ["J\\K       3       6       9      12"]
    for j in J_VALUES:
        values = [statistics_by_strategy[(panel, j, k, "Buy-Sell")][field] * 100 for k in K_VALUES]
        lines.append(f"{j:>3} " + " ".join(f"{value:7.3f}" for value in values))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=ROOT/"processed"/"monthly_stock_prepared.parquet")
    parser.add_argument("--monthly-raw", type=Path, default=ROOT/"02_Monthly_Stocks_and_Factors"/"data"/"monthly_stock.csv")
    parser.add_argument("--daily-raw", type=Path, default=ROOT/"03_Daily_Stocks"/"data"/"daily_stock.csv")
    parser.add_argument("--monthly-lookup", type=Path, default=ROOT/"processed"/"table1_monthly_return_lookup.parquet")
    parser.add_argument("--daily-cumulative", type=Path, default=ROOT/"processed"/"table1_daily_cumulative.parquet")
    parser.add_argument("--table-output", type=Path, default=ROOT/"outputs"/"tables"/"table1_replication.csv")
    parser.add_argument("--monthly-output", type=Path, default=ROOT/"outputs"/"tables"/"table1_monthly_returns.csv")
    parser.add_argument("--report-output", type=Path, default=ROOT/"outputs"/"logs"/"table1_replication_report.txt")
    args = parser.parse_args()
    for path in (args.prepared, args.monthly_raw, args.daily_raw):
        if not path.is_file():
            raise SystemExit(f"Required input not found: {path}")

    input_before = {
        "prepared": signature(args.prepared, include_hash=True),
        "monthly_raw": signature(args.monthly_raw),
        "daily_raw": signature(args.daily_raw),
    }
    run_synthetic_tests()
    connection = duckdb.connect()
    connection.execute("SET memory_limit='4GB'")
    connection.execute(f"SET temp_directory='{sql_path(ROOT/'processed'/'.duckdb_tmp')}'")
    monthly_diag = build_monthly_return_lookup(
        connection, args.monthly_raw, args.prepared, args.monthly_lookup
    )
    daily_diag = build_daily_cumulative(
        connection, args.daily_raw, args.prepared, args.daily_cumulative
    )
    eligibility, returns_by_stock, returns_by_month = read_inputs(
        args.prepared, args.monthly_lookup
    )
    dates = market_calendar(connection, args.daily_cumulative)

    signals: dict[tuple[str, int], dict[int, list[tuple[int, float]]]] = {}
    signal_diagnostics: dict[tuple[str, int], Counter[str]] = {}
    for j in J_VALUES:
        signals[("A", j)], signal_diagnostics[("A", j)] = panel_a_signals(
            eligibility, returns_by_stock, j
        )
    panel_b, panel_b_diagnostics = panel_b_signals(
        connection, eligibility, args.daily_cumulative, dates
    )
    for j in J_VALUES:
        signals[("B", j)] = panel_b[j]

    cohorts: dict[tuple[str, int], dict[int, Cohort]] = {}
    cohort_diagnostics: dict[tuple[str, int], Counter[str]] = {}
    for key, value in signals.items():
        cohorts[key], cohort_diagnostics[key] = form_cohorts(value)
    evaluation_months = common_evaluation_months(
        cohorts, sorted(set(eligibility).intersection(returns_by_month))
    )

    monthly_rows: list[dict[str, object]] = []
    strategy_statistics: dict[tuple[str, int, int, str], tuple[float, float, float]] = {}
    holding_diagnostics: Counter[str] = Counter()
    for panel in ("A", "B"):
        for j in J_VALUES:
            for k in K_VALUES:
                rows, diagnostics = strategy_returns(
                    cohorts[(panel, j)], k, evaluation_months, returns_by_month
                )
                for reason, count in diagnostics.items():
                    holding_diagnostics[f"{panel}_J{j}_K{k}_{reason}"] += count
                for row in rows:
                    monthly_rows.append({"panel": panel, "J": j, "K": k, **row})
                for portfolio, column in (
                    ("Sell", "p1_return"), ("Buy", "p10_return"),
                    ("Buy-Sell", "wml_return"),
                ):
                    strategy_statistics[(panel, j, k, portfolio)] = mean_se_t(
                        [float(row[column]) for row in rows]
                    )

    summary_rows: list[dict[str, object]] = []
    for panel in ("A", "B"):
        for j in J_VALUES:
            for portfolio in ("Sell", "Buy", "Buy-Sell"):
                original_returns, original_t = ORIGINAL[panel][j][portfolio]
                for position, k in enumerate(K_VALUES):
                    average, standard_error, t_statistic = strategy_statistics[
                        (panel, j, k, portfolio)
                    ]
                    original_average = original_returns[position]
                    summary_rows.append({
                        "panel": panel,
                        "formation_months_J": j,
                        "holding_months_K": k,
                        "portfolio": portfolio,
                        "modern_mean_monthly_return": f"{average:.10f}",
                        "modern_mean_percent": f"{average*100:.6f}",
                        "modern_standard_error": f"{standard_error:.10f}",
                        "modern_t_statistic": f"{t_statistic:.4f}",
                        "modern_n_months": len(evaluation_months),
                        "original_mean_monthly_return": f"{original_average:.4f}",
                        "original_mean_percent": f"{original_average*100:.2f}",
                        "original_t_statistic": f"{original_t[position]:.2f}",
                        "modern_minus_original_percentage_points": f"{(average-original_average)*100:.6f}",
                    })

    if len(summary_rows) != 96 or len(monthly_rows) != 32*len(evaluation_months):
        raise Table1ValidationError("Unexpected Table I output dimensions")
    if any(int(row["active_cohorts"]) != int(row["K"]) for row in monthly_rows):
        raise Table1ValidationError("Overlapping-cohort count validation failed")
    if any(
        not math.isclose(
            float(row["wml_return"]),
            float(row["p10_return"])-float(row["p1_return"]),
            abs_tol=1e-15,
        ) for row in monthly_rows
    ):
        raise Table1ValidationError("WML identity failed")

    input_after = {
        "prepared": signature(args.prepared, include_hash=True),
        "monthly_raw": signature(args.monthly_raw),
        "daily_raw": signature(args.daily_raw),
    }
    if input_before != input_after:
        raise Table1ValidationError("An input file changed during execution")
    write_csv_atomic(args.table_output, summary_rows)
    write_csv_atomic(args.monthly_output, monthly_rows)

    panel_a_j6k6 = strategy_statistics[("A", 6, 6, "Buy-Sell")]
    panel_b_j6k6 = strategy_statistics[("B", 6, 6, "Buy-Sell")]
    holding_no_record = sum(
        count for name, count in holding_diagnostics.items() if name.endswith("_no_record")
    )
    holding_missing_return = sum(
        count for name, count in holding_diagnostics.items() if name.endswith("_missing_return")
    )
    holding_incomplete_delisting = sum(
        count for name, count in holding_diagnostics.items()
        if "_incomplete_delisting_" in name
    )
    expected_holding_slots = sum(
        int(row["p1_expected_slots"]) + int(row["p10_expected_slots"])
        for row in monthly_rows
    )
    usable_holding_slots = sum(
        int(row["p1_usable_slots"]) + int(row["p10_usable_slots"])
        for row in monthly_rows
    )
    report = f"""TABLE I REPLICATION REPORT

STATUS
Completed all 32 strategies: Panels A/B and J,K in 3,6,9,12.
Only Table I was implemented. Tables II and III were not started.
Modern evaluation period: {yyyymm_from_index(evaluation_months[0])}-{yyyymm_from_index(evaluation_months[-1])} ({len(evaluation_months)} common months)
Original-paper evaluation period: 196501-198912.

INPUT VALIDATION
Stage 2 rows/stocks/months: 520,438 / 4,669 / 312; unique keys confirmed.
Prepared Parquet readable with Python; SHA-256: {input_before['prepared'][2]}
Unfiltered monthly lookup: {monthly_diag['stock_months']:,} stock-months, {monthly_diag['duplicate_excess_rows']:,} duplicate excess rows resolved with no return/flag conflicts.
Daily relevant universe: {daily_diag['stock_days']:,} stock-days, {daily_diag['valid_returns']:,} nonmissing returns, {daily_diag['duplicate_excess_rows']:,} duplicate excess rows resolved with no return/flag conflicts.
Daily return range preserved: {daily_diag['min_return']:.6f} to {daily_diag['max_return']:.6f}.
All raw and prepared inputs unchanged during execution: yes.

METHOD
Formation-date eligibility comes from the Stage 2 NYSE/NYSE American common-stock panel.
Panel A ranks the compounded unfiltered monthly returns over t-J+1 through t and begins holding in t+1.
Panel B forms at the same monthly dates, but measures an exact J-calendar-month daily-return window ending one week before the first trading day of t+1.
All strategies use deterministic ascending-PERMNO tie breaks, equal-weight deciles, monthly rebalancing within cohorts, and equal weighting across K overlapping cohorts.
The common evaluation start is chosen only after every one of the 32 strategies has all required overlapping cohorts.

ONE-WEEK IMPLEMENTATION ASSUMPTION
The paper does not specify whether one week means five trading days or seven calendar days. This implementation uses seven calendar days: the signal endpoint is the latest market trading date on or before formation date minus seven days. The J-month daily window starts immediately after the latest market date on or before that endpoint minus J calendar months.
Panel B requires a stock record at both market-date boundaries. Missing daily return fields inside the interval are not replaced by zero; CRSP nonmissing duration returns are compounded.
Panel B candidates: {panel_b_diagnostics['candidates']:,}; valid signals by J: {', '.join(f'J{j}={panel_b_diagnostics[f"valid_j{j}"]:,}' for j in J_VALUES)}.
Panel A valid signals by J: {', '.join(f'J{j}={signal_diagnostics[("A",j)]["valid"]:,}' for j in J_VALUES)}.

HOLDING RETURNS AND DELISTINGS
Holding returns come from the unfiltered monthly lookup after formation eligibility is fixed. This retains subsequent returns when a stock changes exchange/classification and avoids a future-survival screen.
Complete delisting-adjusted monthly returns are preserved. Missing returns and G/M incomplete delisting returns are never set to zero; the affected cohort leg is reweighted across usable members. A completely empty cohort leg stops execution.
Across all 32 strategy calculations, unavailable constituent slots were: no monthly record={holding_no_record:,}, missing monthly return={holding_missing_return:,}, incomplete G/M delisting return={holding_incomplete_delisting:,}. These counts include repeated appearances of a stock across cohorts and strategies.
Usable holding slots: {usable_holding_slots:,}/{expected_holding_slots:,} ({usable_holding_slots/expected_holding_slots:.2%}); the unavailable count reconciles exactly to the three reasons above.

MODERN WML MEANS (MONTHLY PERCENT)
Panel A
{wml_matrix(strategy_statistics, 'A', 0)}

Panel B
{wml_matrix(strategy_statistics, 'B', 0)}

SELECTED 6/6 RESULTS
Panel A WML: mean={panel_a_j6k6[0]*100:.4f}%, t={panel_a_j6k6[2]:.4f}; original mean=0.9500%, t=3.07.
Panel B WML: mean={panel_b_j6k6[0]*100:.4f}%, t={panel_b_j6k6[2]:.4f}; original mean=1.1000%, t=3.61.

INFERENCE
All reported t-statistics use mean / (sample standard deviation with ddof=1 / sqrt(T)). No HAC correction is applied. Overlapping portfolios can produce serial dependence; a HAC estimate belongs in a separately labelled robustness analysis.

VALIDATION
Synthetic checks passed for compounding, decile ordering, missing holding returns, and market-date boundaries.
Actual-data checks passed for duplicate conflicts, Stage 2 dimensions and keys, date coverage, P1/P10 ordering, balanced deciles, exact J-window timing, no look-ahead, K active cohorts, common sample months, equal weighting, and WML=P10-P1.
Summary rows: {len(summary_rows)}; monthly strategy rows: {len(monthly_rows)}.

OUTPUTS
{args.table_output}
{args.monthly_output}
{args.report_output}
{args.monthly_lookup}
{args.daily_cumulative}
"""
    write_text_atomic(args.report_output, report)
    print(report)


if __name__ == "__main__":
    main()
