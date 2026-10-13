"""Construct the J=6, K=6 momentum strategy for Table I, Panel A.

Formation month t uses returns from t-5 through t. A cohort is then held in
months t+1 through t+6. Each cohort leg is equal weighted each holding month,
and the monthly strategy return is the equal-weighted mean of six cohorts.

This script reads the Stage 2 Parquet file without modifying it. It writes one
summary table, one monthly return series, and one concise validation report.
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
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import pyarrow.parquet as pq
except ImportError as exc:
    raise SystemExit(
        "pyarrow is required. Run 'python -m pip install -r requirements.txt'."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
J = 6
K = 6
EXPECTED_STAGE2 = {"rows": 520_438, "stocks": 4_669, "months": 312}
REQUIRED_COLUMNS = {
    "permno", "yyyymm", "month", "mth_ret", "return_usable",
    "mth_del_flg",
}


class MomentumValidationError(RuntimeError):
    """The input data or portfolio construction violates a required rule."""


@dataclass(frozen=True)
class StockMonth:
    month_index: int
    yyyymm: int
    return_value: float | None
    return_usable: bool
    delisting_flag: str


@dataclass(frozen=True)
class Cohort:
    formation_month: int
    p1: tuple[int, ...]
    p10: tuple[int, ...]
    eligible_stocks: int
    decile_sizes: tuple[int, ...]
    p1_signal_max: float
    p10_signal_min: float


def month_index(yyyymm: int) -> int:
    year, month = divmod(yyyymm, 100)
    if year < 1900 or not 1 <= month <= 12:
        raise MomentumValidationError(f"Invalid YYYYMM value: {yyyymm}")
    return year * 12 + month - 1


def yyyymm_from_index(value: int) -> int:
    year, zero_based_month = divmod(value, 12)
    return year * 100 + zero_based_month + 1


def month_date_from_index(value: int) -> date:
    year, zero_based_month = divmod(value, 12)
    return date(year, zero_based_month + 1, 1)


def compound(returns: list[float]) -> float:
    wealth = 1.0
    for value in returns:
        wealth *= 1.0 + value
    return wealth - 1.0


def assign_deciles(signals: list[tuple[int, float]]) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Rank by signal, break exact ties by PERMNO, and form near-equal deciles."""
    if len(signals) < 10:
        raise MomentumValidationError(
            f"Cannot form ten portfolios from only {len(signals)} eligible stocks"
        )
    ordered = sorted(signals, key=lambda item: (item[1], item[0]))
    assignment: dict[int, int] = {}
    members = {decile: [] for decile in range(1, 11)}
    size = len(ordered)
    for position, (permno, _) in enumerate(ordered):
        decile = (position * 10) // size + 1
        assignment[permno] = decile
        members[decile].append(permno)
    sizes = [len(members[decile]) for decile in range(1, 11)]
    if len(assignment) != size or max(sizes) - min(sizes) > 1 or min(sizes) == 0:
        raise MomentumValidationError("Decile assignment failed size/uniqueness checks")
    return assignment, members


def equal_weight_return(
    members: tuple[int, ...],
    holding_returns: dict[int, tuple[float | None, bool, str]],
) -> tuple[float, int, Counter[str], set[int]]:
    """Return the mean among currently usable members; never insert a zero."""
    values: list[float] = []
    excluded_reasons: Counter[str] = Counter()
    excluded_members: set[int] = set()
    for permno in members:
        observation = holding_returns.get(permno)
        if observation is None:
            excluded_reasons["no_stock_month_record"] += 1
            excluded_members.add(permno)
            continue
        value, usable, delisting_flag = observation
        if not usable:
            reason = "missing_return" if value is None else f"incomplete_delisting_{delisting_flag}"
            excluded_reasons[reason] += 1
            excluded_members.add(permno)
            continue
        if value is None or not math.isfinite(value) or value < -1:
            raise MomentumValidationError(
                f"A row marked return_usable has invalid return for PERMNO {permno}"
            )
        values.append(value)
    if not values:
        raise MomentumValidationError("A cohort leg has no usable holding returns")
    return statistics.fmean(values), len(values), excluded_reasons, excluded_members


def calculate_signals(
    histories: dict[int, list[StockMonth]],
) -> tuple[dict[int, list[tuple[int, float]]], Counter[str]]:
    signals: dict[int, list[tuple[int, float]]] = defaultdict(list)
    diagnostics: Counter[str] = Counter()
    for permno, history in histories.items():
        for position in range(J - 1, len(history)):
            window = history[position - J + 1 : position + 1]
            current = window[-1].month_index
            if [row.month_index for row in window] != list(range(current - J + 1, current + 1)):
                diagnostics["windows_rejected_nonconsecutive"] += 1
                continue
            if not all(row.return_usable for row in window):
                diagnostics["windows_rejected_unusable_return"] += 1
                continue
            values = [row.return_value for row in window]
            if any(value is None for value in values):
                raise MomentumValidationError("Usable formation return is unexpectedly missing")
            signal = compound([float(value) for value in values])
            if not math.isfinite(signal):
                raise MomentumValidationError(
                    f"Non-finite formation return for PERMNO {permno}, {yyyymm_from_index(current)}"
                )
            signals[current].append((permno, signal))
            diagnostics["valid_signals"] += 1
    return dict(signals), diagnostics


def form_cohorts(signals_by_month: dict[int, list[tuple[int, float]]]) -> tuple[dict[int, Cohort], Counter[str]]:
    cohorts: dict[int, Cohort] = {}
    diagnostics: Counter[str] = Counter()
    for formation_month, signals in sorted(signals_by_month.items()):
        assignment, members = assign_deciles(signals)
        signal_by_permno = dict(signals)
        p1_max = max(signal_by_permno[permno] for permno in members[1])
        p10_min = min(signal_by_permno[permno] for permno in members[10])
        if p1_max > p10_min:
            raise MomentumValidationError("P1/P10 ranking order is invalid")
        if set(assignment) != set(signal_by_permno):
            raise MomentumValidationError("Not every eligible stock received one decile")
        sizes = tuple(len(members[decile]) for decile in range(1, 11))
        cohorts[formation_month] = Cohort(
            formation_month=formation_month,
            p1=tuple(members[1]),
            p10=tuple(members[10]),
            eligible_stocks=len(signals),
            decile_sizes=sizes,
            p1_signal_max=p1_max,
            p10_signal_min=p10_min,
        )
        frequencies = Counter(signal for _, signal in signals)
        diagnostics["formation_months"] += 1
        diagnostics["eligible_stock_formations"] += len(signals)
        diagnostics["exact_tie_groups"] += sum(count > 1 for count in frequencies.values())
        diagnostics["stocks_in_exact_ties"] += sum(
            count for count in frequencies.values() if count > 1
        )
    return cohorts, diagnostics


def build_monthly_returns(
    cohorts: dict[int, Cohort],
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]],
) -> tuple[list[dict[str, object]], Counter[str], set[tuple[int, int]]]:
    rows: list[dict[str, object]] = []
    diagnostics: Counter[str] = Counter()
    excluded_stock_months: set[tuple[int, int]] = set()
    for holding_month in sorted(returns_by_month):
        formation_months = list(range(holding_month - K, holding_month))
        if not all(month in cohorts for month in formation_months):
            diagnostics["months_without_six_cohorts"] += 1
            continue
        if formation_months[-1] >= holding_month:
            raise MomentumValidationError("Look-ahead detected in cohort alignment")
        p1_cohort_returns: list[float] = []
        p10_cohort_returns: list[float] = []
        p1_expected = p1_available = p10_expected = p10_available = 0
        month_returns = returns_by_month[holding_month]
        for formation_month in formation_months:
            cohort = cohorts[formation_month]
            age = holding_month - formation_month
            if not 1 <= age <= K:
                raise MomentumValidationError("Holding cohort age is outside 1..6")
            p1_value, p1_count, p1_reasons, p1_excluded = equal_weight_return(
                cohort.p1, month_returns
            )
            p10_value, p10_count, p10_reasons, p10_excluded = equal_weight_return(
                cohort.p10, month_returns
            )
            p1_cohort_returns.append(p1_value)
            p10_cohort_returns.append(p10_value)
            p1_expected += len(cohort.p1)
            p1_available += p1_count
            p10_expected += len(cohort.p10)
            p10_available += p10_count
            for reason, count in p1_reasons.items():
                diagnostics[f"p1_excluded_slots_{reason}"] += count
            for reason, count in p10_reasons.items():
                diagnostics[f"p10_excluded_slots_{reason}"] += count
            excluded_stock_months.update((permno, holding_month) for permno in p1_excluded)
            excluded_stock_months.update((permno, holding_month) for permno in p10_excluded)
        if len(p1_cohort_returns) != K or len(p10_cohort_returns) != K:
            raise MomentumValidationError("Monthly return does not contain six cohorts")
        p1 = statistics.fmean(p1_cohort_returns)
        p10 = statistics.fmean(p10_cohort_returns)
        rows.append({
            "month": month_date_from_index(holding_month).isoformat(),
            "yyyymm": yyyymm_from_index(holding_month),
            "p1_return": p1,
            "p10_return": p10,
            "wml_return": p10 - p1,
            "active_cohorts": K,
            "p1_expected_constituent_slots": p1_expected,
            "p1_usable_constituent_slots": p1_available,
            "p10_expected_constituent_slots": p10_expected,
            "p10_usable_constituent_slots": p10_available,
        })
    if not rows:
        raise MomentumValidationError("No full-overlap monthly portfolio returns were produced")
    return rows, diagnostics, excluded_stock_months


def time_series_statistics(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 2:
        raise MomentumValidationError("At least two monthly returns are required")
    average = statistics.fmean(values)
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    if standard_error == 0:
        raise MomentumValidationError("Zero standard error in monthly return series")
    return average, standard_error, average / standard_error


def run_synthetic_tests() -> list[str]:
    # Six-month signal uses t-5..t and excludes the next month's return.
    base_month = 2000 * 12
    history = {
        1: [StockMonth(base_month + i, yyyymm_from_index(base_month + i), 0.01, True, "N") for i in range(6)],
        2: [StockMonth(base_month + i, yyyymm_from_index(base_month + i), 0.02, True, "N") for i in range(6)],
    }
    signals, _ = calculate_signals(history)
    expected = (1.01 ** 6) - 1
    if not math.isclose(dict(signals[base_month + 5])[1], expected, rel_tol=0, abs_tol=1e-14):
        raise MomentumValidationError("Synthetic compounding test failed")
    history[1].append(StockMonth(base_month + 6, yyyymm_from_index(base_month + 6), 9.0, True, "N"))
    revised, _ = calculate_signals(history)
    if not math.isclose(dict(revised[base_month + 5])[1], expected, rel_tol=0, abs_tol=1e-14):
        raise MomentumValidationError("Synthetic no-look-ahead test failed")

    missing_history = {
        1: [
            StockMonth(base_month + i, yyyymm_from_index(base_month + i), 0.01, i != 2, "N")
            for i in range(6)
        ]
    }
    missing_signals, _ = calculate_signals(missing_history)
    if missing_signals:
        raise MomentumValidationError("Synthetic missing-formation-return test failed")

    assignment, members = assign_deciles([(permno, float(permno)) for permno in range(1, 21)])
    if any(len(members[decile]) != 2 for decile in range(1, 11)):
        raise MomentumValidationError("Synthetic equal-decile-size test failed")
    if set(members[1]) != {1, 2} or set(members[10]) != {19, 20} or len(assignment) != 20:
        raise MomentumValidationError("Synthetic rank-order test failed")

    value, available, reasons, _ = equal_weight_return(
        (1, 2, 3),
        {1: (0.10, True, "N"), 2: (None, False, "N"), 3: (0.20, True, "N")},
    )
    if not math.isclose(value, 0.15) or available != 2 or reasons["missing_return"] != 1:
        raise MomentumValidationError("Synthetic missing-holding-return test failed")

    synthetic_cohorts = {
        base_month + month: Cohort(base_month + month, (1,), (2,), 10, (1,) * 10, 0.0, 1.0)
        for month in range(6)
    }
    synthetic_returns = {
        base_month + 6: {1: (-0.01, True, "N"), 2: (0.02, True, "N")}
    }
    monthly, _, _ = build_monthly_returns(synthetic_cohorts, synthetic_returns)
    if len(monthly) != 1 or monthly[0]["active_cohorts"] != 6:
        raise MomentumValidationError("Synthetic overlapping-cohort test failed")
    if not math.isclose(float(monthly[0]["wml_return"]), 0.03):
        raise MomentumValidationError("Synthetic WML arithmetic test failed")
    return [
        "six-month compounding and no-look-ahead",
        "consecutive usable formation history",
        "deterministic equal-size decile ranking",
        "missing holding returns excluded rather than set to zero",
        "six-cohort overlap and WML arithmetic",
    ]


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


def read_and_validate_input(path: Path):
    parquet_file = pq.ParquetFile(path)
    schema_names = set(parquet_file.schema_arrow.names)
    missing_columns = REQUIRED_COLUMNS.difference(schema_names)
    if missing_columns:
        raise MomentumValidationError(f"Stage 2 Parquet lacks columns: {sorted(missing_columns)}")
    table = pq.read_table(path, columns=sorted(REQUIRED_COLUMNS))
    data = table.to_pydict()
    if table.num_rows != EXPECTED_STAGE2["rows"]:
        raise MomentumValidationError(
            f"Stage 2 row count {table.num_rows:,} != {EXPECTED_STAGE2['rows']:,}"
        )
    histories: dict[int, list[StockMonth]] = defaultdict(list)
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    keys: set[tuple[int, int]] = set()
    observed_months: set[int] = set()
    previous_by_stock: dict[int, int] = {}
    for permno, yyyymm, month_value, return_value, usable, delisting_flag in zip(
        data["permno"], data["yyyymm"], data["month"], data["mth_ret"],
        data["return_usable"], data["mth_del_flg"], strict=True,
    ):
        index = month_index(yyyymm)
        if month_value != month_date_from_index(index):
            raise MomentumValidationError(f"Invalid standardized month for {(permno, yyyymm)}")
        key = (permno, index)
        if key in keys:
            raise MomentumValidationError(f"Duplicate Stage 2 key: {(permno, yyyymm)}")
        keys.add(key)
        prior = previous_by_stock.get(permno)
        if prior is not None and index <= prior:
            raise MomentumValidationError(f"Stage 2 rows are not ordered for PERMNO {permno}")
        previous_by_stock[permno] = index
        if return_value is not None and not math.isfinite(return_value):
            raise MomentumValidationError(f"Non-finite return for {(permno, yyyymm)}")
        observation = StockMonth(
            index, yyyymm, return_value, bool(usable), delisting_flag or ""
        )
        histories[permno].append(observation)
        returns_by_month[index][permno] = (return_value, bool(usable), delisting_flag or "")
        observed_months.add(index)
    if len(histories) != EXPECTED_STAGE2["stocks"]:
        raise MomentumValidationError("Unexpected Stage 2 distinct PERMNO count")
    if len(observed_months) != EXPECTED_STAGE2["months"]:
        raise MomentumValidationError("Unexpected Stage 2 distinct month count")
    if sorted(observed_months) != list(range(min(observed_months), max(observed_months) + 1)):
        raise MomentumValidationError("Stage 2 calendar months are not consecutive")
    return histories, dict(returns_by_month), parquet_file.num_row_groups


def write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    os.replace(temporary, path)


def format_counter(counter: Counter[str], prefix: str) -> str:
    selected = sorted((key, value) for key, value in counter.items() if key.startswith(prefix))
    return ", ".join(f"{key.removeprefix(prefix)}={value:,}" for key, value in selected) or "none"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "processed" / "monthly_stock_prepared.parquet",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=ROOT / "outputs" / "tables" / "table1_panel_a_j6_k6.csv",
    )
    parser.add_argument(
        "--monthly-output",
        type=Path,
        default=ROOT / "outputs" / "tables" / "table1_panel_a_j6_k6_monthly.csv",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=ROOT / "outputs" / "logs" / "momentum_j6_k6_report.txt",
    )
    args = parser.parse_args()
    raw_path = ROOT / "02_Monthly_Stocks_and_Factors" / "data" / "monthly_stock.csv"
    if not args.input.is_file():
        raise SystemExit(f"Prepared input not found: {args.input}")
    if not raw_path.is_file():
        raise SystemExit(f"Raw input not found for unchanged-file validation: {raw_path}")

    raw_before = file_signature(raw_path)
    prepared_before = file_signature(args.input, include_hash=True)
    synthetic_tests = run_synthetic_tests()
    histories, returns_by_month, row_groups = read_and_validate_input(args.input)
    signals_by_month, signal_diagnostics = calculate_signals(histories)
    cohorts, cohort_diagnostics = form_cohorts(signals_by_month)
    monthly_rows, holding_diagnostics, excluded_stock_months = build_monthly_returns(
        cohorts, returns_by_month
    )

    first_output = int(monthly_rows[0]["yyyymm"])
    last_output = int(monthly_rows[-1]["yyyymm"])
    expected_first = yyyymm_from_index(min(returns_by_month) + J + K - 1)
    expected_last = yyyymm_from_index(max(returns_by_month))
    if first_output != expected_first or last_output != expected_last:
        raise MomentumValidationError(
            f"Unexpected output range {first_output}-{last_output}; expected "
            f"{expected_first}-{expected_last}"
        )
    expected_month_count = max(returns_by_month) - (min(returns_by_month) + J + K - 1) + 1
    if len(monthly_rows) != expected_month_count:
        raise MomentumValidationError("Unexpected number of full-overlap return months")
    if any(int(row["active_cohorts"]) != K for row in monthly_rows):
        raise MomentumValidationError("An output month does not contain six cohorts")
    if any(
        not math.isclose(
            float(row["wml_return"]),
            float(row["p10_return"]) - float(row["p1_return"]),
            rel_tol=0,
            abs_tol=1e-15,
        )
        for row in monthly_rows
    ):
        raise MomentumValidationError("WML identity validation failed")

    portfolio_columns = {
        "P1": "p1_return", "P10": "p10_return", "WML (P10-P1)": "wml_return"
    }
    summary_rows: list[dict[str, object]] = []
    stats_by_portfolio: dict[str, tuple[float, float, float]] = {}
    for portfolio, column in portfolio_columns.items():
        values = [float(row[column]) for row in monthly_rows]
        average, standard_error, t_statistic = time_series_statistics(values)
        stats_by_portfolio[portfolio] = (average, standard_error, t_statistic)
        summary_rows.append({
            "panel": "A: no skip",
            "formation_months_J": J,
            "holding_months_K": K,
            "portfolio": portfolio,
            "mean_monthly_return": f"{average:.10f}",
            "mean_monthly_return_percent": f"{average * 100:.6f}",
            "standard_error": f"{standard_error:.10f}",
            "t_statistic": f"{t_statistic:.4f}",
            "n_months": len(values),
        })

    raw_after = file_signature(raw_path)
    prepared_after = file_signature(args.input, include_hash=True)
    if raw_before != raw_after:
        raise MomentumValidationError("Raw CSV changed during Stage 3")
    if prepared_before != prepared_after:
        raise MomentumValidationError("Prepared Parquet changed during Stage 3")

    write_csv_atomic(
        args.summary_output,
        list(summary_rows[0]),
        summary_rows,
    )
    write_csv_atomic(
        args.monthly_output,
        list(monthly_rows[0]),
        monthly_rows,
    )

    formation_sizes = [cohort.eligible_stocks for cohort in cohorts.values()]
    all_decile_sizes = [size for cohort in cohorts.values() for size in cohort.decile_sizes]
    p1_slots = sum(int(row["p1_expected_constituent_slots"]) for row in monthly_rows)
    p1_usable = sum(int(row["p1_usable_constituent_slots"]) for row in monthly_rows)
    p10_slots = sum(int(row["p10_expected_constituent_slots"]) for row in monthly_rows)
    p10_usable = sum(int(row["p10_usable_constituent_slots"]) for row in monthly_rows)
    absent_pairs: set[tuple[int, int]] = set()
    for row in monthly_rows:
        holding_month = month_index(int(row["yyyymm"]))
        for formation_month in range(holding_month - K, holding_month):
            cohort = cohorts[formation_month]
            for permno in cohort.p1 + cohort.p10:
                if permno not in returns_by_month[holding_month]:
                    absent_pairs.add((permno, holding_month))
    last_prepared_month = {
        permno: history[-1].month_index for permno, history in histories.items()
    }
    post_final_pairs = {
        pair for pair in absent_pairs if pair[1] > last_prepared_month[pair[0]]
    }
    within_span_pairs = absent_pairs.difference(post_final_pairs)
    post_final_stocks = {permno for permno, _ in post_final_pairs}
    last_flags = Counter(histories[permno][-1].delisting_flag for permno in post_final_stocks)
    report = f"""STAGE 3 MOMENTUM REPORT: TABLE I, PANEL A, J=6, K=6

STATUS
Completed and validated. Only the no-skip 6/6 strategy was constructed.
Prepared Parquet readable by Python: yes ({row_groups} row groups, ZSTD supported)
Prepared SHA-256: {prepared_before[2]}
Raw CSV unchanged during Stage 3: yes
Prepared Parquet unchanged during Stage 3: yes

STAGE 2 INPUT VALIDATION
Rows: {sum(len(history) for history in histories.values()):,} (expected 520,438)
Distinct PERMNOs: {len(histories):,} (expected 4,669)
Distinct months: {len(returns_by_month):,} (expected 312)
Duplicate PERMNO-month keys: 0
Month range: {yyyymm_from_index(min(returns_by_month))}-{yyyymm_from_index(max(returns_by_month))}

PORTFOLIO TIMING AND ELIGIBILITY
Formation month t uses exactly t-5 through t; holding begins in t+1.
All six formation months must exist and have return_usable=True.
Valid stock-formation signals: {signal_diagnostics['valid_signals']:,}
Rejected nonconsecutive six-row windows: {signal_diagnostics['windows_rejected_nonconsecutive']:,}
Rejected six-month windows containing an unusable return: {signal_diagnostics['windows_rejected_unusable_return']:,}
Formation months ranked: {cohort_diagnostics['formation_months']:,}
Eligible stocks per formation month: {min(formation_sizes):,} to {max(formation_sizes):,}
Decile sizes across formations: {min(all_decile_sizes):,} to {max(all_decile_sizes):,}
Exact signal-tie groups: {cohort_diagnostics['exact_tie_groups']:,}; tied stock-formations: {cohort_diagnostics['stocks_in_exact_ties']:,}

MONTHLY STRATEGY SERIES
Full six-cohort months: {len(monthly_rows):,}
Return period: {first_output}-{last_output}
P1 usable constituent slots: {p1_usable:,}/{p1_slots:,}
P10 usable constituent slots: {p10_usable:,}/{p10_slots:,}
Unique selected stock-months with an unusable/absent return: {len(excluded_stock_months):,}
Unique selected stock-months absent from the prepared panel: {len(absent_pairs):,}
  after the stock's final prepared row: {len(post_final_pairs):,} pairs across {len(post_final_stocks):,} stocks
  gaps inside the stock's prepared-data span: {len(within_span_pairs):,} pairs
Final prepared-row delisting flags for those {len(post_final_stocks):,} stocks: {', '.join(f'{key or "blank"}={value:,}' for key, value in sorted(last_flags.items()))}
P1 exclusions by reason: {format_counter(holding_diagnostics, 'p1_excluded_slots_')}
P10 exclusions by reason: {format_counter(holding_diagnostics, 'p10_excluded_slots_')}

RESULTS (MONTHLY PERCENT)
P1 losers: mean={stats_by_portfolio['P1'][0] * 100:.4f}, t={stats_by_portfolio['P1'][2]:.4f}
P10 winners: mean={stats_by_portfolio['P10'][0] * 100:.4f}, t={stats_by_portfolio['P10'][2]:.4f}
WML (P10-P1): mean={stats_by_portfolio['WML (P10-P1)'][0] * 100:.4f}, t={stats_by_portfolio['WML (P10-P1)'][2]:.4f}
Standard errors are conventional time-series standard errors: sample SD (ddof=1) / sqrt(T).
No autocorrelation or heteroskedasticity correction is applied in this paper-style table.

VALIDATION CHECKS
Synthetic tests passed: {'; '.join(synthetic_tests)}.
Actual-data checks passed: Stage 2 dimensions/schema; unique keys; standardized and consecutive calendar months; exact six-month signal windows; one decile per eligible stock; decile sizes differ by at most one; P1 signals do not exceed P10 signals; holding begins after formation; exactly six cohorts per reported month; equal weighting; WML=P10-P1.

IMPLEMENTATION ASSUMPTIONS AND UNRESOLVED ISSUES
The paper states equal-weight deciles, overlapping cohorts, and monthly rebalancing but does not specify an exact-tie rule. Exact signal ties are split deterministically by ascending PERMNO.
A cohort retains its original membership. In a holding month, its leg is rebalanced equally across members with return_usable=True. Missing returns and incomplete G/M delisting returns are omitted and the remaining weights are renormalized; no zero is inserted and no stock is excluded using future information.
Stage 2 already includes complete A/P/V delisting returns. The six G/M observations remain unusable because their delisting return is incomplete. The selected-portfolio impact is counted above. Alternative delisting-return imputation requires a separately justified robustness analysis.
An absent prepared stock-month can mean a true security exit or that its exchange/security classification no longer passes the monthly Stage 2 filter. The prepared panel alone cannot distinguish every case. Before treating the 6/6 estimates as final, verify whether holding returns should be drawn from an unfiltered return lookup after eligibility is fixed at formation; this could reduce the no-record omissions without imposing a future-survival rule.
The reported series begins only when all six overlapping cohorts are present, so the start-up months are excluded.
The modern 2000-2025 data are a sample-period deviation from the paper's 1965-1989 analysis; numerical equality with the original table is not expected.
Conventional t-statistics match the stated table convention used here, but overlapping strategies can create serial dependence. A HAC robustness statistic should be considered later and clearly separated from the replication statistic.

OUTPUTS
{args.summary_output}
{args.monthly_output}
{args.report_output}
"""
    write_text_atomic(args.report_output, report)
    print(report)


if __name__ == "__main__":
    main()
