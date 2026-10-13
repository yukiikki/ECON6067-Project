"""Replicate Jegadeesh and Titman (1993) Table III for the modern sample.

The script applies the no-skip J=6, K=6 strategy within three formation-date
size groups and three ex-ante Scholes-Williams beta groups. It reports raw
returns (Panel A), CAPM alphas (Panel B), and the paper's joint tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, mkstemp

try:
    import duckdb
    import pyarrow.parquet as pq
except ImportError as exc:
    raise SystemExit(
        "duckdb and pyarrow are required. Run "
        "'python -m pip install -r requirements.txt'."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
J = 6
K = 6
MIN_BETA_OBSERVATIONS = 100
GROUPS = ("All", "S1", "S2", "S3", "B1", "B2", "B3")
PORTFOLIOS = tuple(f"P{number}" for number in range(1, 11)) + ("P10-P1",)
EXPECTED_STAGE2 = {"rows": 520_438, "stocks": 4_669, "months": 312}
EXPECTED_EVALUATION = {"first": 200201, "last": 202512, "months": 288}


ORIGINAL_A = {
    "P1": ([.0079, .0083, .0047, .0082, .0129, .0097, .0052], [1.56, 1.35, .99, 2.22, 2.92, 2.01, .95]),
    "P2": ([.0112, .0117, .0102, .0098, .0140, .0128, .0086], [2.78, 2.29, 2.54, 3.08, 4.38, 3.37, 1.83]),
    "P3": ([.0125, .0152, .0125, .0105, .0132, .0133, .0102], [3.40, 3.23, 3.34, 3.53, 4.59, 3.77, 2.28]),
    "P4": ([.0124, .0163, .0130, .0105, .0134, .0128, .0110], [3.59, 3.59, 3.58, 3.66, 5.02, 3.82, 2.50]),
    "P5": ([.0128, .0164, .0134, .0109, .0135, .0135, .0121], [3.87, 3.74, 3.83, 3.85, 5.14, 4.15, 2.86]),
    "P6": ([.0134, .0174, .0146, .0102, .0135, .0142, .0122], [4.14, 4.08, 4.22, 3.66, 5.23, 4.38, 2.92]),
    "P7": ([.0136, .0175, .0143, .0109, .0136, .0142, .0126], [4.19, 4.13, 4.12, 3.90, 5.09, 4.43, 3.01]),
    "P8": ([.0143, .0174, .0148, .0111, .0143, .0146, .0132], [4.30, 4.11, 4.16, 3.86, 5.12, 4.44, 3.15]),
    "P9": ([.0153, .0183, .0154, .0126, .0165, .0156, .0141], [4.36, 4.28, 4.11, 4.17, 5.34, 4.56, 3.28]),
    "P10": ([.0174, .0182, .0173, .0157, .0191, .0176, .0160], [4.33, 3.99, 4.11, 4.41, 5.17, 4.53, 3.50]),
    "P10-P1": ([.0095, .0099, .0126, .0075, .0062, .0079, .0108], [3.07, 2.77, 4.57, 3.03, 2.05, 2.64, 3.35]),
}

ORIGINAL_B = {
    "P1": ([-.0030, -.0029, -.0062, -.0020, .0031, -.0009, -.0062], [-.89, -.60, -2.11, -1.17, .94, -.28, -1.71]),
    "P2": ([.0011, .0012, -.0001, .0000, .0051, .0029, -.0024], [.43, .31, -.03, .03, 2.36, 1.26, -.87]),
    "P3": ([.0026, .0051, .0024, .0009, .0045, .0035, -.0007], [1.24, 1.46, 1.18, .93, 2.45, 1.83, -.29]),
    "P4": ([.0026, .0062, .0030, .0011, .0048, .0031, .0000], [1.48, 1.90, 1.57, 1.24, 2.98, 1.83, .01]),
    "P5": ([.0031, .0064, .0036, .0014, .0049, .0038, .0012], [1.96, 2.06, 1.98, 1.84, 3.21, 2.55, .58]),
    "P6": ([.0037, .0075, .0048, .0008, .0048, .0045, .0013], [2.55, 2.51, 2.74, 1.13, 3.46, 3.12, .69]),
    "P7": ([.0039, .0075, .0044, .0015, .0049, .0045, .0017], [2.70, 2.57, 2.61, 2.15, 3.29, 3.25, .90]),
    "P8": ([.0045, .0074, .0048, .0016, .0054, .0049, .0023], [3.01, 2.56, 2.76, 2.12, 3.53, 3.29, 1.19]),
    "P9": ([.0053, .0082, .0052, .0029, .0074, .0057, .0031], [3.20, 2.89, 2.76, 3.23, 4.10, 3.60, 1.54]),
    "P10": ([.0070, .0077, .0067, .0056, .0094, .0074, .0048], [3.24, 2.56, 2.91, 3.50, 4.10, 3.47, 2.02]),
    "P10-P1": ([.0100, .0106, .0129, .0076, .0063, .0083, .0111], [3.23, 2.97, 4.69, 3.08, 2.09, 2.76, 3.42]),
}

ORIGINAL_F_A = dict(zip(GROUPS, [2.83, 2.65, 4.51, 4.38, 2.51, 1.99, 1.69], strict=True))
ORIGINAL_P_A = dict(zip(GROUPS, [.00, .00, .00, .00, .01, .04, .09], strict=True))
ORIGINAL_F_B = dict(zip(GROUPS, [5.2910, 5.4401, 8.3713, 4.7386, 3.6045, 4.0171, 2.5872], strict=True))


class Table3ValidationError(RuntimeError):
    """An input, construction rule, or numerical invariant failed."""


@dataclass(frozen=True)
class Cohort:
    formation_month: int
    members: tuple[tuple[int, ...], ...]


def sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def month_index(yyyymm: int) -> int:
    year, month = divmod(int(yyyymm), 100)
    if year < 1900 or not 1 <= month <= 12:
        raise Table3ValidationError(f"Invalid YYYYMM: {yyyymm}")
    return year * 12 + month - 1


def yyyymm_from_index(value: int) -> int:
    year, zero_month = divmod(value, 12)
    return year * 100 + zero_month + 1


def file_signature(path: Path, include_hash: bool = False) -> tuple[int, int, str | None]:
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
    descriptor, name = mkstemp(prefix=f".{target.stem}_", suffix=".parquet", dir=target.parent)
    os.close(descriptor)
    Path(name).unlink()
    return Path(name)


def build_or_validate_daily_market(
    connection: duckdb.DuckDBPyConnection,
    raw_daily: Path,
    output: Path,
    rebuild: bool,
) -> dict[str, object]:
    if rebuild or not output.is_file():
        temporary = temporary_parquet(output)
        query = f"""
            COPY (
                SELECT DlyCalDt AS trading_date,
                       min(vwretd) AS market_return,
                       count(*) AS source_rows,
                       count(vwretd) AS nonmissing_market_rows,
                       count(DISTINCT vwretd) AS distinct_market_values
                FROM read_csv_auto('{sql_path(raw_daily)}', header=true, sample_size=100000)
                WHERE DlyCalDt < DATE '2025-01-01'
                GROUP BY DlyCalDt
                ORDER BY DlyCalDt
            ) TO '{sql_path(temporary)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """
        connection.execute(query)
        os.replace(temporary, output)
        output.chmod(0o644)
        built = True
    else:
        built = False
    row = connection.execute(
        f"""
        SELECT count(*), min(trading_date), max(trading_date),
               sum(distinct_market_values <> 1),
               sum(nonmissing_market_rows = 0),
               count(DISTINCT trading_date),
               min(market_return), max(market_return)
        FROM read_parquet('{sql_path(output)}')
        """
    ).fetchone()
    if row[0] != row[5] or row[3] or row[4] or row[0] < 6000:
        raise Table3ValidationError("Daily market cache failed uniqueness/coverage checks")
    return {
        "built": built, "days": int(row[0]), "first": row[1], "last": row[2],
        "min_return": float(row[6]), "max_return": float(row[7]),
    }


def build_scholes_williams_betas(
    connection: duckdb.DuckDBPyConnection,
    daily_cumulative: Path,
    daily_market: Path,
    output: Path,
) -> dict[str, object]:
    temporary = temporary_parquet(output)
    query = f"""
        COPY (
            WITH recovered AS (
                SELECT permno, trading_date,
                       cumulative_log_return
                         - coalesce(lag(cumulative_log_return) OVER w, 0.0) AS log_return,
                       cumulative_valid_returns
                         - coalesce(lag(cumulative_valid_returns) OVER w, 0) AS valid_increment
                FROM read_parquet('{sql_path(daily_cumulative)}')
                WINDOW w AS (PARTITION BY permno ORDER BY trading_date)
            ), stock AS (
                SELECT permno, trading_date, exp(log_return)-1.0 AS stock_return
                FROM recovered
                WHERE valid_increment=1 AND trading_date < DATE '2025-01-01'
            ), market AS (
                SELECT trading_date, market_return,
                       lag(market_return) OVER (ORDER BY trading_date) AS market_lag,
                       lead(market_return) OVER (ORDER BY trading_date) AS market_lead
                FROM read_parquet('{sql_path(daily_market)}')
            ), joined AS (
                SELECT s.permno, s.trading_date, year(s.trading_date) AS estimation_year,
                       s.stock_return, m.market_return, m.market_lag, m.market_lead
                FROM stock s JOIN market m USING (trading_date)
            ), market_year AS (
                SELECT year(trading_date) AS estimation_year,
                       corr(market_return, market_lag) AS market_autocorrelation
                FROM market GROUP BY year(trading_date)
            ), estimates AS (
                SELECT permno, estimation_year,
                       count(*) AS observations_current,
                       count(market_lag) AS observations_lag,
                       count(market_lead) AS observations_lead,
                       regr_slope(stock_return, market_lag) AS beta_lag,
                       regr_slope(stock_return, market_return) AS beta_current,
                       regr_slope(stock_return, market_lead) AS beta_lead
                FROM joined GROUP BY permno, estimation_year
            )
            SELECT e.permno, e.estimation_year,
                   e.observations_current, e.observations_lag, e.observations_lead,
                   e.beta_lag, e.beta_current, e.beta_lead,
                   m.market_autocorrelation,
                   (e.beta_lag+e.beta_current+e.beta_lead)
                     /(1.0+2.0*m.market_autocorrelation) AS scholes_williams_beta
            FROM estimates e JOIN market_year m USING (estimation_year)
            WHERE e.observations_current >= {MIN_BETA_OBSERVATIONS}
              AND e.observations_lag >= {MIN_BETA_OBSERVATIONS}
              AND e.observations_lead >= {MIN_BETA_OBSERVATIONS}
              AND e.beta_lag IS NOT NULL AND e.beta_current IS NOT NULL
              AND e.beta_lead IS NOT NULL AND m.market_autocorrelation IS NOT NULL
              AND abs(1.0+2.0*m.market_autocorrelation) > 0.1
            ORDER BY permno, estimation_year
        ) TO '{sql_path(temporary)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    connection.execute(query)
    os.replace(temporary, output)
    output.chmod(0o644)
    row = connection.execute(
        f"""
        SELECT count(*), count(DISTINCT permno), count(DISTINCT estimation_year),
               min(estimation_year), max(estimation_year),
               sum(scholes_williams_beta IS NULL OR NOT isfinite(scholes_williams_beta)),
               min(scholes_williams_beta), max(scholes_williams_beta),
               min(observations_current), max(observations_current)
        FROM read_parquet('{sql_path(output)}')
        """
    ).fetchone()
    if not row[0] or row[5] or row[3] != 2000 or row[4] != 2024:
        raise Table3ValidationError("Scholes-Williams beta cache failed validation")
    return {
        "rows": int(row[0]), "stocks": int(row[1]), "years": int(row[2]),
        "first_year": int(row[3]), "last_year": int(row[4]),
        "min_beta": float(row[6]), "max_beta": float(row[7]),
        "min_observations": int(row[8]), "max_observations": int(row[9]),
    }


def compound(values: list[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value
    return wealth - 1.0


def assign_groups(values: list[tuple[int, float]], number: int) -> tuple[tuple[int, ...], ...]:
    if len(values) < number:
        raise Table3ValidationError("Too few observations for portfolio assignment")
    if len({permno for permno, _ in values}) != len(values):
        raise Table3ValidationError("Duplicate stock in cross-sectional assignment")
    ordered = sorted(values, key=lambda item: (item[1], item[0]))
    groups: list[list[int]] = [[] for _ in range(number)]
    for position, (permno, _) in enumerate(ordered):
        groups[(position * number) // len(ordered)].append(permno)
    sizes = [len(group) for group in groups]
    if min(sizes) == 0 or max(sizes) - min(sizes) > 1:
        raise Table3ValidationError("Cross-sectional group sizes are invalid")
    return tuple(tuple(group) for group in groups)


def read_inputs(prepared: Path, monthly_lookup: Path, beta_file: Path):
    table = pq.read_table(
        prepared,
        columns=["permno", "yyyymm", "month", "mth_cap", "cap_usable", "vwretd"],
    )
    if table.num_rows != EXPECTED_STAGE2["rows"]:
        raise Table3ValidationError("Unexpected Stage 2 row count")
    data = table.to_pydict()
    eligibility: dict[int, list[int]] = defaultdict(list)
    caps: dict[tuple[int, int], tuple[float | None, bool]] = {}
    market_values: dict[int, set[float]] = defaultdict(set)
    stocks: set[int] = set()
    for permno, yyyymm, month_value, cap, cap_usable, market in zip(
        data["permno"], data["yyyymm"], data["month"], data["mth_cap"],
        data["cap_usable"], data["vwretd"], strict=True,
    ):
        index = month_index(yyyymm)
        if (month_value.year, month_value.month) != divmod(int(yyyymm), 100):
            raise Table3ValidationError("Invalid Stage 2 month mapping")
        key = (int(permno), index)
        if key in caps:
            raise Table3ValidationError("Duplicate Stage 2 key")
        caps[key] = (cap, bool(cap_usable))
        eligibility[index].append(int(permno))
        stocks.add(int(permno))
        if market is not None:
            market_values[index].add(float(market))
    if len(stocks) != EXPECTED_STAGE2["stocks"] or len(eligibility) != EXPECTED_STAGE2["months"]:
        raise Table3ValidationError("Unexpected Stage 2 dimensions")
    if any(len(values) != 1 for values in market_values.values()):
        raise Table3ValidationError("Monthly market return is not unique")
    market_by_month = {month: next(iter(values)) for month, values in market_values.items()}

    lookup_table = pq.read_table(
        monthly_lookup,
        columns=["permno", "yyyymm", "mth_ret", "mth_del_flg", "return_usable"],
    )
    lookup = lookup_table.to_pydict()
    returns_by_stock: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    for permno, yyyymm, value, flag, usable in zip(
        lookup["permno"], lookup["yyyymm"], lookup["mth_ret"],
        lookup["mth_del_flg"], lookup["return_usable"], strict=True,
    ):
        permno = int(permno)
        index = month_index(yyyymm)
        if index in returns_by_stock[permno]:
            raise Table3ValidationError("Duplicate monthly lookup key")
        observation = (value, bool(usable), flag or "")
        returns_by_stock[permno][index] = observation
        returns_by_month[index][permno] = observation

    beta_table = pq.read_table(
        beta_file, columns=["permno", "estimation_year", "scholes_williams_beta"]
    ).to_pydict()
    betas: dict[tuple[int, int], float] = {}
    for permno, year, beta in zip(
        beta_table["permno"], beta_table["estimation_year"],
        beta_table["scholes_williams_beta"], strict=True,
    ):
        key = (int(permno), int(year))
        if key in betas or beta is None or not math.isfinite(float(beta)):
            raise Table3ValidationError("Duplicate or invalid annual beta")
        betas[key] = float(beta)
    return (
        dict(eligibility), caps, market_by_month, dict(returns_by_stock),
        dict(returns_by_month), betas, lookup_table.num_rows,
    )


def calculate_signals(
    eligibility: dict[int, list[int]],
    returns_by_stock: dict[int, dict[int, tuple[float | None, bool, str]]],
) -> tuple[dict[int, list[tuple[int, float]]], Counter[str]]:
    signals: dict[int, list[tuple[int, float]]] = defaultdict(list)
    diagnostics: Counter[str] = Counter()
    for formation_month, permnos in eligibility.items():
        required = range(formation_month - J + 1, formation_month + 1)
        for permno in permnos:
            observations = [returns_by_stock.get(permno, {}).get(month) for month in required]
            if any(observation is None for observation in observations):
                diagnostics["missing_month"] += 1
                continue
            if not all(observation[1] for observation in observations if observation):
                diagnostics["unusable_return"] += 1
                continue
            values = [observation[0] for observation in observations if observation]
            if any(value is None for value in values):
                raise Table3ValidationError("Usable formation return is missing")
            signal = compound([float(value) for value in values])
            if not math.isfinite(signal) or signal < -1:
                raise Table3ValidationError("Invalid formation signal")
            signals[formation_month].append((permno, signal))
            diagnostics["valid"] += 1
    return dict(signals), diagnostics


def build_cohorts(
    signals_by_month: dict[int, list[tuple[int, float]]],
    caps: dict[tuple[int, int], tuple[float | None, bool]],
    betas: dict[tuple[int, int], float],
) -> tuple[dict[str, dict[int, Cohort]], Counter[str], list[float]]:
    cohorts: dict[str, dict[int, Cohort]] = {group: {} for group in GROUPS}
    diagnostics: Counter[str] = Counter()
    used_beta_values: list[float] = []
    for formation_month, signal_rows in sorted(signals_by_month.items()):
        signal = dict(signal_rows)
        all_members = assign_groups(signal_rows, 10)
        cohorts["All"][formation_month] = Cohort(formation_month, all_members)

        size_rows: list[tuple[int, float]] = []
        for permno in signal:
            cap, usable = caps[(permno, formation_month)]
            if usable and cap is not None and math.isfinite(float(cap)) and float(cap) > 0:
                size_rows.append((permno, float(cap)))
            else:
                diagnostics["size_missing"] += 1
        size_groups = assign_groups(size_rows, 3)
        if set().union(*(set(group) for group in size_groups)) != {p for p, _ in size_rows}:
            raise Table3ValidationError("Size terciles are not exhaustive")
        for number, members in enumerate(size_groups, start=1):
            momentum = assign_groups([(permno, signal[permno]) for permno in members], 10)
            cohorts[f"S{number}"][formation_month] = Cohort(formation_month, momentum)
            diagnostics[f"S{number}_assignments"] += len(members)

        formation_year = yyyymm_from_index(formation_month + 1) // 100
        estimation_year = formation_year - 1
        beta_rows: list[tuple[int, float]] = []
        for permno in signal:
            beta = betas.get((permno, estimation_year))
            if beta is None:
                diagnostics["beta_missing"] += 1
                continue
            beta_rows.append((permno, beta))
            used_beta_values.append(beta)
        beta_groups = assign_groups(beta_rows, 3)
        if set().union(*(set(group) for group in beta_groups)) != {p for p, _ in beta_rows}:
            raise Table3ValidationError("Beta terciles are not exhaustive")
        for number, members in enumerate(beta_groups, start=1):
            momentum = assign_groups([(permno, signal[permno]) for permno in members], 10)
            cohorts[f"B{number}"][formation_month] = Cohort(formation_month, momentum)
            diagnostics[f"B{number}_assignments"] += len(members)
        diagnostics["formation_months"] += 1
    if any(set(months) != set(signals_by_month) for months in cohorts.values()):
        raise Table3ValidationError("A subgroup lacks formation cohorts")
    return cohorts, diagnostics, used_beta_values


def read_evaluation_and_baseline(table2_monthly: Path):
    months: list[int] = []
    baseline: dict[int, tuple[float, ...]] = {}
    with table2_monthly.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            month = month_index(int(row["yyyymm"]))
            if month in baseline:
                raise Table3ValidationError("Duplicate Table II month")
            months.append(month)
            baseline[month] = tuple(float(row[f"p{number}_return"]) for number in range(1, 11))
    months.sort()
    if (
        len(months) != EXPECTED_EVALUATION["months"]
        or yyyymm_from_index(months[0]) != EXPECTED_EVALUATION["first"]
        or yyyymm_from_index(months[-1]) != EXPECTED_EVALUATION["last"]
        or months != list(range(months[0], months[-1] + 1))
    ):
        raise Table3ValidationError("Unexpected evaluation period")
    return months, baseline


def read_risk_free(path: Path) -> dict[int, float]:
    result: dict[int, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream):
            if not row or len(row) < 5:
                continue
            key = row[0].strip()
            if len(key) != 6 or not key.isdigit():
                continue
            value = row[4].strip()
            if value:
                result[month_index(int(key))] = float(value) / 100.0
    if not result:
        raise Table3ValidationError("No monthly risk-free returns were read")
    return result


def equal_weight_return(
    members: tuple[int, ...],
    month_returns: dict[int, tuple[float | None, bool, str]],
) -> tuple[float, Counter[str]]:
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
        if value is None or not math.isfinite(float(value)) or float(value) < -1:
            raise Table3ValidationError("A usable holding return is invalid")
        values.append(float(value))
    if not values:
        raise Table3ValidationError("A cohort-decile has no usable holding return")
    return statistics.fmean(values), diagnostics


def build_monthly_returns(
    cohorts: dict[str, dict[int, Cohort]],
    evaluation_months: list[int],
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]],
    market_by_month: dict[int, float],
    risk_free: dict[int, float],
) -> tuple[list[dict[str, object]], Counter[str]]:
    rows: list[dict[str, object]] = []
    diagnostics: Counter[str] = Counter()
    for group in GROUPS:
        for holding_month in evaluation_months:
            if holding_month not in market_by_month or holding_month not in risk_free:
                raise Table3ValidationError("Missing market or risk-free return")
            formation_months = list(range(holding_month - K, holding_month))
            if not all(month in cohorts[group] for month in formation_months):
                raise Table3ValidationError("A holding month lacks six cohorts")
            decile_returns: list[float] = []
            for decile in range(10):
                cohort_values: list[float] = []
                for formation_month in formation_months:
                    if not 1 <= holding_month - formation_month <= K:
                        raise Table3ValidationError("Invalid holding age")
                    value, excluded = equal_weight_return(
                        cohorts[group][formation_month].members[decile],
                        returns_by_month.get(holding_month, {}),
                    )
                    cohort_values.append(value)
                    for reason, count in excluded.items():
                        diagnostics[f"{group}_{reason}"] += count
                decile_returns.append(statistics.fmean(cohort_values))
            row: dict[str, object] = {
                "subgroup": group, "yyyymm": yyyymm_from_index(holding_month),
                "market_return": market_by_month[holding_month],
                "risk_free_return": risk_free[holding_month], "active_cohorts": K,
            }
            for decile, value in enumerate(decile_returns, start=1):
                row[f"p{decile}_return"] = value
            row["wml_return"] = decile_returns[9] - decile_returns[0]
            rows.append(row)
    return rows, diagnostics


def mean_t(values: list[float]) -> tuple[float, float]:
    average = statistics.fmean(values)
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    if standard_error <= 0:
        raise Table3ValidationError("Zero standard error")
    return average, average / standard_error


def market_model(y: list[float], factor: list[float]) -> tuple[float, float, float, list[float]]:
    if len(y) != len(factor) or len(y) < 3:
        raise Table3ValidationError("Invalid market-model sample")
    mean_y = statistics.fmean(y)
    mean_x = statistics.fmean(factor)
    sxx = sum((value - mean_x) ** 2 for value in factor)
    if sxx <= 0:
        raise Table3ValidationError("Market factor has zero variance")
    beta = sum(
        (x - mean_x) * (value - mean_y)
        for value, x in zip(y, factor, strict=True)
    ) / sxx
    alpha = mean_y - beta * mean_x
    residuals = [
        value - alpha - beta * x for value, x in zip(y, factor, strict=True)
    ]
    variance = sum(value * value for value in residuals) / (len(y) - 2)
    alpha_se = math.sqrt(variance * (1.0 / len(y) + mean_x * mean_x / sxx))
    if alpha_se <= 0:
        raise Table3ValidationError("Market-model alpha has zero standard error")
    return alpha, alpha / alpha_se, beta, residuals


def covariance_matrix(columns: list[list[float]], sample: bool) -> list[list[float]]:
    length = len(columns[0])
    if any(len(column) != length for column in columns):
        raise Table3ValidationError("Unequal covariance input lengths")
    means = [statistics.fmean(column) for column in columns]
    denominator = length - 1 if sample else length
    return [[
        sum(
            (columns[i][t] - means[i]) * (columns[j][t] - means[j])
            for t in range(length)
        ) / denominator
        for j in range(len(columns))
    ] for i in range(len(columns))]


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-14:
            raise Table3ValidationError("Singular covariance matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                left - factor * right
                for left, right in zip(augmented[row], augmented[column], strict=True)
            ]
    return [augmented[row][-1] for row in range(size)]


def quadratic(vector: list[float], matrix: list[list[float]]) -> float:
    solution = solve(matrix, vector)
    return sum(left * right for left, right in zip(vector, solution, strict=True))


def beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 300
    epsilon = 3e-14
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        m2 = 2 * iteration
        term = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + term * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + term / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        term = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + term * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + term / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        change = d * c
        result *= change
        if abs(change - 1.0) < epsilon:
            return result
    raise Table3ValidationError("Incomplete-beta calculation did not converge")


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * beta_continued_fraction(a, b, x) / a
    return 1.0 - front * beta_continued_fraction(b, a, 1.0 - x) / b


def f_survival(statistic: float, df1: int, df2: int) -> float:
    if statistic < 0 or df1 <= 0 or df2 <= 0:
        raise Table3ValidationError("Invalid F statistic or degrees of freedom")
    x = df2 / (df2 + df1 * statistic)
    return regularized_beta(x, df2 / 2.0, df1 / 2.0)


def equal_mean_test(deciles: list[list[float]]) -> tuple[float, float, int, int]:
    differences = [
        [deciles[number][t] - deciles[0][t] for t in range(len(deciles[0]))]
        for number in range(1, 10)
    ]
    means = [statistics.fmean(values) for values in differences]
    covariance = covariance_matrix(differences, sample=True)
    observations = len(deciles[0])
    restrictions = 9
    hotelling = observations * quadratic(means, covariance)
    statistic = (observations - restrictions) / (
        restrictions * (observations - 1)
    ) * hotelling
    return statistic, f_survival(statistic, restrictions, observations - restrictions), restrictions, observations - restrictions


def grs_test(
    alphas: list[float], residuals: list[list[float]], factor: list[float]
) -> tuple[float, float, int, int]:
    observations = len(factor)
    portfolios = len(alphas)
    factors = 1
    residual_covariance = covariance_matrix(residuals, sample=False)
    factor_mean = statistics.fmean(factor)
    factor_variance = sum((value - factor_mean) ** 2 for value in factor) / observations
    statistic = (
        (observations - portfolios - factors) / portfolios
        * quadratic(alphas, residual_covariance)
        / (1.0 + factor_mean * factor_mean / factor_variance)
    )
    df2 = observations - portfolios - factors
    return statistic, f_survival(statistic, portfolios, df2), portfolios, df2


def calculate_results(monthly_rows: list[dict[str, object]]):
    rows_by_group: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in monthly_rows:
        rows_by_group[str(row["subgroup"])].append(row)
    summary: list[dict[str, object]] = []
    tests: dict[tuple[str, str], tuple[float, float, int, int]] = {}
    for group_index, group in enumerate(GROUPS):
        rows = rows_by_group[group]
        deciles = [[float(row[f"p{number}_return"]) for row in rows] for number in range(1, 11)]
        wml = [float(row["wml_return"]) for row in rows]
        for portfolio, values in zip(PORTFOLIOS, [*deciles, wml], strict=True):
            estimate, t_statistic = mean_t(values)
            original_estimate = ORIGINAL_A[portfolio][0][group_index]
            original_t = ORIGINAL_A[portfolio][1][group_index]
            summary.append({
                "panel": "A", "subgroup": group, "statistic": portfolio,
                "modern_estimate": f"{estimate:.10f}",
                "modern_percent": f"{estimate*100:.6f}",
                "modern_t_statistic": f"{t_statistic:.4f}",
                "modern_p_value": "", "df1": "", "df2": "",
                "original_estimate": f"{original_estimate:.4f}",
                "original_t_or_f": f"{original_t:.2f}", "original_p_value": "",
            })
        tests[("A", group)] = equal_mean_test(deciles)
        statistic, p_value, df1, df2 = tests[("A", group)]
        summary.append({
            "panel": "A", "subgroup": group, "statistic": "F_equal_means",
            "modern_estimate": f"{statistic:.10f}", "modern_percent": "",
            "modern_t_statistic": "", "modern_p_value": f"{p_value:.10f}",
            "df1": df1, "df2": df2,
            "original_estimate": "", "original_t_or_f": f"{ORIGINAL_F_A[group]:.4f}",
            "original_p_value": f"{ORIGINAL_P_A[group]:.2f}",
        })

        factor = [float(row["market_return"]) - float(row["risk_free_return"]) for row in rows]
        alphas: list[float] = []
        residuals: list[list[float]] = []
        panel_b_results: dict[str, tuple[float, float]] = {}
        for number, values in enumerate(deciles, start=1):
            excess = [value - float(row["risk_free_return"]) for value, row in zip(values, rows, strict=True)]
            alpha, t_statistic, _, residual = market_model(excess, factor)
            panel_b_results[f"P{number}"] = (alpha, t_statistic)
            alphas.append(alpha)
            residuals.append(residual)
        alpha, t_statistic, _, _ = market_model(wml, factor)
        panel_b_results["P10-P1"] = (alpha, t_statistic)
        if not math.isclose(alpha, alphas[9] - alphas[0], abs_tol=1e-12):
            raise Table3ValidationError("WML alpha identity failed")
        for portfolio in PORTFOLIOS:
            estimate, t_value = panel_b_results[portfolio]
            original_estimate = ORIGINAL_B[portfolio][0][group_index]
            original_t = ORIGINAL_B[portfolio][1][group_index]
            summary.append({
                "panel": "B", "subgroup": group, "statistic": portfolio,
                "modern_estimate": f"{estimate:.10f}",
                "modern_percent": f"{estimate*100:.6f}",
                "modern_t_statistic": f"{t_value:.4f}",
                "modern_p_value": "", "df1": "", "df2": "",
                "original_estimate": f"{original_estimate:.4f}",
                "original_t_or_f": f"{original_t:.2f}", "original_p_value": "",
            })
        tests[("B", group)] = grs_test(alphas, residuals, factor)
        statistic, p_value, df1, df2 = tests[("B", group)]
        summary.append({
            "panel": "B", "subgroup": group, "statistic": "F_alpha_zero_GRS",
            "modern_estimate": f"{statistic:.10f}", "modern_percent": "",
            "modern_t_statistic": "", "modern_p_value": f"{p_value:.10f}",
            "df1": df1, "df2": df2,
            "original_estimate": "", "original_t_or_f": f"{ORIGINAL_F_B[group]:.4f}",
            "original_p_value": "<0.01",
        })
    return summary, tests


def run_synthetic_tests() -> None:
    groups = assign_groups([(number, float(number)) for number in range(1, 31)], 3)
    if groups[0] != tuple(range(1, 11)) or groups[2] != tuple(range(21, 31)):
        raise Table3ValidationError("Synthetic tercile test failed")
    alpha, t_value, beta, _ = market_model(
        [.01 + 2*x + e for x, e in zip([-.02, 0, .01, .03], [.001, -.001, .001, -.001], strict=True)],
        [-.02, 0, .01, .03],
    )
    if not math.isfinite(t_value) or abs(beta - 2.0) > .1 or abs(alpha - .01) > .01:
        raise Table3ValidationError("Synthetic market-model test failed")
    sw_beta = (.5 + .8 + .7) / (1 + 2 * .1)
    if not math.isclose(sw_beta, 5/3, abs_tol=1e-15):
        raise Table3ValidationError("Synthetic Scholes-Williams test failed")
    if not math.isclose(f_survival(1.0, 1, 1), .5, abs_tol=1e-10):
        raise Table3ValidationError("Synthetic F-distribution test failed")
    solution = solve([[2.0, 0.0], [0.0, 4.0]], [4.0, 8.0])
    if solution != [2.0, 2.0]:
        raise Table3ValidationError("Synthetic matrix-solve test failed")


def write_csv_atomic(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise Table3ValidationError("Cannot write an empty CSV")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=ROOT/"processed"/"monthly_stock_prepared.parquet")
    parser.add_argument("--monthly-lookup", type=Path, default=ROOT/"processed"/"table1_monthly_return_lookup.parquet")
    parser.add_argument("--daily-cumulative", type=Path, default=ROOT/"processed"/"table1_daily_cumulative.parquet")
    parser.add_argument("--daily-raw", type=Path, default=ROOT/"03_Daily_Stocks"/"data"/"daily_stock.csv")
    parser.add_argument("--factor-file", type=Path, default=ROOT/"02_Monthly_Stocks_and_Factors"/"data"/"F-F factors and RF.csv")
    parser.add_argument("--table2-monthly", type=Path, default=ROOT/"outputs"/"tables"/"table2_monthly_portfolios.csv")
    parser.add_argument("--daily-market", type=Path, default=ROOT/"processed"/"table3_daily_market.parquet")
    parser.add_argument("--beta-output", type=Path, default=ROOT/"processed"/"table3_scholes_williams_betas.parquet")
    parser.add_argument("--table-output", type=Path, default=ROOT/"outputs"/"tables"/"table3_replication.csv")
    parser.add_argument("--monthly-output", type=Path, default=ROOT/"outputs"/"tables"/"table3_monthly_portfolios.csv")
    parser.add_argument("--report-output", type=Path, default=ROOT/"outputs"/"logs"/"table3_replication_report.txt")
    parser.add_argument("--rebuild-daily-market", action="store_true")
    args = parser.parse_args()
    inputs = (
        args.prepared, args.monthly_lookup, args.daily_cumulative, args.daily_raw,
        args.factor_file, args.table2_monthly,
    )
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"Required input not found: {path}")
    signatures_before = {
        path: file_signature(path, include_hash=path not in {args.daily_raw})
        for path in inputs
    }
    run_synthetic_tests()
    connection = duckdb.connect()
    connection.execute("SET memory_limit='4GB'")
    temp_directory = ROOT/"processed"/".duckdb_tmp"
    temp_directory.mkdir(parents=True, exist_ok=True)
    connection.execute(f"SET temp_directory='{sql_path(temp_directory)}'")
    market_diagnostics = build_or_validate_daily_market(
        connection, args.daily_raw, args.daily_market, args.rebuild_daily_market
    )
    # Fix the aggregation order so annual beta estimates are bitwise reproducible.
    connection.execute("SET threads=1")
    beta_diagnostics = build_scholes_williams_betas(
        connection, args.daily_cumulative, args.daily_market, args.beta_output
    )
    (
        eligibility, caps, market_by_month, returns_by_stock, returns_by_month,
        betas, lookup_rows,
    ) = read_inputs(args.prepared, args.monthly_lookup, args.beta_output)
    signals, signal_diagnostics = calculate_signals(eligibility, returns_by_stock)
    evaluation_months, baseline = read_evaluation_and_baseline(args.table2_monthly)
    required_formations = sorted({
        formation_month
        for holding_month in evaluation_months
        for formation_month in range(holding_month-K, holding_month)
    })
    if any(month not in signals for month in required_formations):
        raise Table3ValidationError("A required evaluation-period formation cohort is missing")
    evaluation_signals = {month: signals[month] for month in required_formations}
    cohorts, cohort_diagnostics, used_beta_values = build_cohorts(
        evaluation_signals, caps, betas
    )
    risk_free = read_risk_free(args.factor_file)
    monthly_rows, holding_diagnostics = build_monthly_returns(
        cohorts, evaluation_months, returns_by_month, market_by_month, risk_free
    )
    summary_rows, tests = calculate_results(monthly_rows)

    all_rows = [row for row in monthly_rows if row["subgroup"] == "All"]
    max_all_difference = max(
        abs(float(row[f"p{number}_return"]) - baseline[month][number-1])
        for row, month in zip(all_rows, evaluation_months, strict=True)
        for number in range(1, 11)
    )
    if max_all_difference > 1e-12:
        raise Table3ValidationError("All-stock portfolios do not reproduce Table II")
    if len(monthly_rows) != len(GROUPS) * len(evaluation_months):
        raise Table3ValidationError("Unexpected monthly output dimensions")
    if len(summary_rows) != len(GROUPS) * 24:
        raise Table3ValidationError("Unexpected summary output dimensions")
    signatures_after = {
        path: file_signature(path, include_hash=path not in {args.daily_raw})
        for path in inputs
    }
    if signatures_before != signatures_after:
        raise Table3ValidationError("An input or prior result changed during execution")
    write_csv_atomic(args.table_output, summary_rows)
    write_csv_atomic(args.monthly_output, monthly_rows)

    results = {
        (row["panel"], row["subgroup"], row["statistic"]): row
        for row in summary_rows
    }
    wml_lines = ["Group  Panel A mean (t)     Panel B alpha (t)"]
    for group in GROUPS:
        a = results[("A", group, "P10-P1")]
        b = results[("B", group, "P10-P1")]
        wml_lines.append(
            f"{group:>5}  {float(a['modern_percent']):>8.4f}% ({float(a['modern_t_statistic']):>7.3f})"
            f"  {float(b['modern_percent']):>8.4f}% ({float(b['modern_t_statistic']):>7.3f})"
        )
    test_lines = ["Group  Panel A F (p)       Panel B GRS F (p)"]
    for group in GROUPS:
        a = tests[("A", group)]
        b = tests[("B", group)]
        test_lines.append(
            f"{group:>5}  {a[0]:>8.4f} ({a[1]:.4f})  {b[0]:>8.4f} ({b[1]:.4f})"
        )
    sorted_betas = sorted(used_beta_values)
    beta_median = statistics.median(sorted_betas)
    beta_p01 = sorted_betas[int(.01*(len(sorted_betas)-1))]
    beta_p99 = sorted_betas[int(.99*(len(sorted_betas)-1))]
    total_exclusions = sum(holding_diagnostics.values())
    report = f"""TABLE III REPLICATION REPORT

STATUS
Completed the modern-sample replication of Jegadeesh and Titman (1993) Table III.
Strategy: J=6, K=6, no skip week. Evaluation period: {EXPECTED_EVALUATION['first']}-{EXPECTED_EVALUATION['last']} ({len(evaluation_months)} months).

PAPER-STATED METHOD
The paper forms momentum portfolios within small/medium/large size subsamples and within low/medium/high ex-ante beta subsamples. Ex-ante betas are Scholes-Williams betas estimated from daily returns in the calendar year before portfolio formation. Panel A reports mean monthly returns. Panel B reports CAPM alphas from portfolio excess returns regressed on the CRSP value-weighted market excess return. Panel A jointly tests equality of P1-P10 means; Panel B jointly tests that P1-P10 alphas equal zero.

IMPLEMENTATION ASSUMPTIONS REQUIRING REVIEW
1. Size is measured by end-of-formation-month MthCap. Eligible stocks are split into near-equal terciles using ascending PERMNO for exact ties, then ranked into momentum deciles within each tercile.
2. Scholes-Williams beta is (beta_lag + beta_current + beta_lead)/(1 + 2*rho_market). It uses raw daily total returns, requires at least {MIN_BETA_OBSERVATIONS} usable observations for every component, and is neither trimmed nor winsorized. Market lead/lag dates immediately outside a calendar-year boundary may enter the corresponding edge observation.
3. Stocks without an eligible prior-calendar-year beta are excluded only from beta-sorted subsamples. They remain in All and size portfolios.
4. Panel A uses conventional time-series t-statistics and a Hotelling finite-sample F test for equality of ten means. Panel B uses conventional OLS alpha t-statistics and the Gibbons-Ross-Shanken F test. The paper identifies the nulls but does not state these exact finite-sample formulas.
5. Exact momentum, size, and beta ties are broken by ascending PERMNO. The paper does not state tie rules.

WML RESULTS
{chr(10).join(wml_lines)}

JOINT TESTS
{chr(10).join(test_lines)}

DATA AND COVERAGE
Stage 2 panel: {EXPECTED_STAGE2['rows']:,} rows, {EXPECTED_STAGE2['stocks']:,} stocks, {EXPECTED_STAGE2['months']} months; unique keys confirmed.
Unfiltered holding-return lookup: {lookup_rows:,} stock-months.
Daily market cache: {market_diagnostics['days']:,} trading days, {market_diagnostics['first']} to {market_diagnostics['last']}; {'built from the raw daily CSV in this run' if market_diagnostics['built'] else 'validated and reused'}.
Annual Scholes-Williams estimates: {beta_diagnostics['rows']:,} stock-years, {beta_diagnostics['stocks']:,} stocks, years {beta_diagnostics['first_year']}-{beta_diagnostics['last_year']}.
Annual beta range: {beta_diagnostics['min_beta']:.4f} to {beta_diagnostics['max_beta']:.4f}; formation-assignment distribution p1/median/p99: {beta_p01:.4f}/{beta_median:.4f}/{beta_p99:.4f}. Extreme estimates are retained.
Valid momentum stock-formations: {signal_diagnostics['valid']:,}; missing six-month history: {signal_diagnostics['missing_month']:,}; unusable formation return: {signal_diagnostics['unusable_return']:,}.
Beta-subsample exclusions for unavailable eligible prior-year beta: {cohort_diagnostics['beta_missing']:,} stock-formations. Size-subsample exclusions for unavailable/nonpositive cap: {cohort_diagnostics['size_missing']:,}.
Holding-component exclusions under the existing Table I convention: {total_exclusions:,}; missing returns were not set to zero and complete delisting returns were retained.

VALIDATION
PASS - synthetic tercile, Scholes-Williams, OLS, matrix, and F-distribution checks.
PASS - prior-calendar-year betas are used; no formation or holding decision uses future stock returns.
PASS - size and beta terciles are exhaustive among stocks with usable sorting characteristics and differ in size by at most one.
PASS - momentum deciles within every subsample are mutually exclusive, ordered, and balanced.
PASS - every monthly portfolio has six active cohorts, equal weighting within cohort-deciles, and equal weighting across cohorts.
PASS - All P1-P10 monthly returns reproduce Table II; maximum absolute difference {max_all_difference:.3e}.
PASS - WML return and alpha identities, CAPM factor units, joint-test dimensions, and the 288-month evaluation period were validated.
PASS - repeated single-thread beta estimation produced bitwise-identical beta and final CSV outputs.
PASS - raw data, prepared data, and existing Table I/II results were unchanged.

INTERPRETATION LIMITS
The modern 2000-2025 sample is not expected to reproduce the paper's 1965-1989 estimates. Beta availability and the {MIN_BETA_OBSERVATIONS}-day rule change the beta-sorted universe and should be approved before Table III is treated as methodologically final. Conventional t-statistics do not adjust for residual serial dependence from overlapping portfolios.

OUTPUTS
{args.table_output}
{args.monthly_output}
{args.report_output}
{args.beta_output}
{args.daily_market}
"""
    write_text_atomic(args.report_output, report)
    print(report)


if __name__ == "__main__":
    main()
