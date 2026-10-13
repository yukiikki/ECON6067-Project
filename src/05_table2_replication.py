"""Replicate Jegadeesh and Titman (1993) Table II for the modern sample.

Table II uses the J=6, K=6 strategy without a skip week. This script forms
all ten momentum deciles, estimates their post-ranking market betas against
the CRSP value-weighted index, and reports formation-date market
capitalization. Existing Table I and input files are read-only.
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
EXPECTED_EVALUATION = {"first": 200201, "last": 202512, "months": 288}

ORIGINAL = {
    "P1": (1.36, 208.24),
    "P2": (1.19, 480.07),
    "P3": (1.14, 545.31),
    "P4": (1.11, 618.85),
    "P5": (1.09, 692.89),
    "P6": (1.08, 702.51),
    "P7": (1.09, 738.09),
    "P8": (1.12, 758.87),
    "P9": (1.17, 680.18),
    "P10": (1.28, 495.13),
    "P10-P1": (-0.08, None),
}


class Table2ValidationError(RuntimeError):
    """An input, portfolio, or output invariant failed."""


@dataclass(frozen=True)
class Cohort:
    formation_month: int
    members: tuple[tuple[int, ...], ...]
    eligible_stocks: int


def month_index(yyyymm: int) -> int:
    year, month = divmod(int(yyyymm), 100)
    if year < 1900 or not 1 <= month <= 12:
        raise Table2ValidationError(f"Invalid YYYYMM: {yyyymm}")
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


def compound(values: list[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value
    return wealth - 1.0


def assign_deciles(signals: list[tuple[int, float]]) -> tuple[tuple[int, ...], ...]:
    """Assign near-equal deciles using ascending PERMNO for exact ties."""
    if len(signals) < 10:
        raise Table2ValidationError("Fewer than ten stocks are available for ranking")
    if len({permno for permno, _ in signals}) != len(signals):
        raise Table2ValidationError("A stock appears twice in a formation month")
    ordered = sorted(signals, key=lambda item: (item[1], item[0]))
    members: list[list[int]] = [[] for _ in range(10)]
    for position, (permno, _) in enumerate(ordered):
        members[(position * 10) // len(ordered)].append(permno)
    sizes = [len(group) for group in members]
    if not sizes or min(sizes) == 0 or max(sizes) - min(sizes) > 1:
        raise Table2ValidationError("Decile size validation failed")
    return tuple(tuple(group) for group in members)


def ols_alpha_beta(y: list[float], x: list[float]) -> tuple[float, float]:
    """Return OLS intercept and slope for y = alpha + beta*x + error."""
    if len(y) != len(x) or len(y) < 3:
        raise Table2ValidationError("Insufficient or unmatched observations for beta")
    mean_y = statistics.fmean(y)
    mean_x = statistics.fmean(x)
    denominator = sum((value - mean_x) ** 2 for value in x)
    if denominator <= 0:
        raise Table2ValidationError("Market return variance is zero")
    beta = sum(
        (market - mean_x) * (portfolio - mean_y)
        for portfolio, market in zip(y, x, strict=True)
    ) / denominator
    alpha = mean_y - beta * mean_x
    return alpha, beta


def validate_market_cap_scale(raw_monthly: Path, target_observations: int = 1000) -> tuple[int, float]:
    """Check MthCap = abs(MthPrc)*ShrOut; ShrOut is documented in thousands."""
    deviations: list[float] = []
    with raw_monthly.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"MthCap", "MthPrc", "ShrOut"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise Table2ValidationError("Raw monthly file lacks market-cap scale fields")
        for row in reader:
            try:
                cap = float(row["MthCap"])
                price = abs(float(row["MthPrc"]))
                shares = float(row["ShrOut"])
            except (TypeError, ValueError):
                continue
            if cap <= 0 or price <= 0 or shares <= 0:
                continue
            deviations.append(abs(cap / (price * shares) - 1.0))
            if len(deviations) == target_observations:
                break
    if len(deviations) != target_observations or max(deviations) > 1e-5:
        raise Table2ValidationError("Could not validate the supplied MthCap scale")
    return len(deviations), max(deviations)


def read_inputs(prepared: Path, monthly_lookup: Path):
    table = pq.read_table(
        prepared,
        columns=[
            "permno", "yyyymm", "month", "mth_cap", "cap_usable", "vwretd"
        ],
    )
    if table.num_rows != EXPECTED_STAGE2["rows"]:
        raise Table2ValidationError("Unexpected Stage 2 row count")
    data = table.to_pydict()
    eligibility: dict[int, list[int]] = defaultdict(list)
    formation_caps: dict[tuple[int, int], tuple[float | None, bool]] = {}
    market_values: dict[int, set[float]] = defaultdict(set)
    stocks: set[int] = set()
    for permno, yyyymm, month_value, cap, cap_usable, market in zip(
        data["permno"], data["yyyymm"], data["month"], data["mth_cap"],
        data["cap_usable"], data["vwretd"], strict=True,
    ):
        index = month_index(yyyymm)
        if (month_value.year, month_value.month) != divmod(int(yyyymm), 100):
            raise Table2ValidationError("Invalid Stage 2 month mapping")
        key = (int(permno), index)
        if key in formation_caps:
            raise Table2ValidationError(f"Duplicate Stage 2 key: {key}")
        formation_caps[key] = (cap, bool(cap_usable))
        eligibility[index].append(int(permno))
        stocks.add(int(permno))
        if market is not None:
            if not math.isfinite(float(market)):
                raise Table2ValidationError("Non-finite market return")
            market_values[index].add(float(market))
    if len(stocks) != EXPECTED_STAGE2["stocks"] or len(eligibility) != EXPECTED_STAGE2["months"]:
        raise Table2ValidationError("Unexpected Stage 2 stock or month count")
    if any(len(values) != 1 for values in market_values.values()):
        raise Table2ValidationError("The value-weighted market return is not unique by month")
    market_by_month = {month: next(iter(values)) for month, values in market_values.items()}

    lookup_table = pq.read_table(
        monthly_lookup,
        columns=["permno", "yyyymm", "mth_ret", "mth_del_flg", "return_usable"],
    )
    lookup_rows = lookup_table.num_rows
    lookup = lookup_table.to_pydict()
    returns_by_stock: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]] = defaultdict(dict)
    for permno, yyyymm, value, flag, usable in zip(
        lookup["permno"], lookup["yyyymm"], lookup["mth_ret"],
        lookup["mth_del_flg"], lookup["return_usable"], strict=True,
    ):
        index = month_index(yyyymm)
        permno = int(permno)
        if index in returns_by_stock[permno]:
            raise Table2ValidationError("Duplicate key in monthly return lookup")
        observation = (value, bool(usable), flag or "")
        returns_by_stock[permno][index] = observation
        returns_by_month[index][permno] = observation
    return (
        dict(eligibility), formation_caps, market_by_month,
        dict(returns_by_stock), dict(returns_by_month), table.num_rows, lookup_rows,
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
                raise Table2ValidationError("Usable formation return is missing")
            signal = compound([float(value) for value in values])
            if not math.isfinite(signal) or signal < -1:
                raise Table2ValidationError("Invalid formation return")
            signals[formation_month].append((permno, signal))
            diagnostics["valid"] += 1
    return dict(signals), diagnostics


def form_cohorts(
    signals_by_month: dict[int, list[tuple[int, float]]]
) -> tuple[dict[int, Cohort], Counter[str]]:
    cohorts: dict[int, Cohort] = {}
    diagnostics: Counter[str] = Counter()
    for formation_month, signals in sorted(signals_by_month.items()):
        members = assign_deciles(signals)
        flattened = [permno for group in members for permno in group]
        if len(flattened) != len(set(flattened)) or len(flattened) != len(signals):
            raise Table2ValidationError("Cohort membership is not exhaustive and unique")
        signal_by_permno = dict(signals)
        for left, right in zip(members[:-1], members[1:], strict=True):
            if max(signal_by_permno[p] for p in left) > min(signal_by_permno[p] for p in right):
                raise Table2ValidationError("Decile signal ordering failed")
        cohorts[formation_month] = Cohort(formation_month, members, len(signals))
        frequencies = Counter(signal for _, signal in signals)
        diagnostics["formation_months"] += 1
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
        if value is None or not math.isfinite(float(value)) or float(value) < -1:
            raise Table2ValidationError("A usable holding return is invalid")
        values.append(float(value))
    if not values:
        raise Table2ValidationError("A cohort-decile has no usable holding return")
    return statistics.fmean(values), len(values), diagnostics


def read_evaluation_months(table1_monthly: Path) -> tuple[list[int], dict[int, tuple[float, float]]]:
    months: list[int] = []
    baseline: dict[int, tuple[float, float]] = {}
    with table1_monthly.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["panel"] == "A" and int(row["J"]) == J and int(row["K"]) == K:
                month = month_index(int(row["yyyymm"]))
                if month in baseline:
                    raise Table2ValidationError("Duplicate Table I J=6, K=6 month")
                months.append(month)
                baseline[month] = (float(row["p1_return"]), float(row["p10_return"]))
    months.sort()
    if (
        len(months) != EXPECTED_EVALUATION["months"]
        or yyyymm_from_index(months[0]) != EXPECTED_EVALUATION["first"]
        or yyyymm_from_index(months[-1]) != EXPECTED_EVALUATION["last"]
        or months != list(range(months[0], months[-1] + 1))
    ):
        raise Table2ValidationError("Unexpected Table I evaluation period")
    return months, baseline


def build_monthly_portfolios(
    cohorts: dict[int, Cohort],
    evaluation_months: list[int],
    returns_by_month: dict[int, dict[int, tuple[float | None, bool, str]]],
    market_by_month: dict[int, float],
) -> tuple[list[dict[str, object]], Counter[str]]:
    rows: list[dict[str, object]] = []
    diagnostics: Counter[str] = Counter()
    for holding_month in evaluation_months:
        if holding_month not in market_by_month:
            raise Table2ValidationError("Missing market return in evaluation month")
        formation_months = list(range(holding_month - K, holding_month))
        if not all(month in cohorts for month in formation_months):
            raise Table2ValidationError("A holding month lacks six formation cohorts")
        decile_returns: list[float] = []
        expected_slots: list[int] = []
        usable_slots: list[int] = []
        for decile in range(10):
            cohort_returns: list[float] = []
            expected = usable = 0
            for formation_month in formation_months:
                if not 1 <= holding_month - formation_month <= K:
                    raise Table2ValidationError("Invalid cohort holding age")
                members = cohorts[formation_month].members[decile]
                value, count, excluded = equal_weight_return(
                    members, returns_by_month.get(holding_month, {})
                )
                cohort_returns.append(value)
                expected += len(members)
                usable += count
                for reason, number in excluded.items():
                    diagnostics[f"P{decile + 1}_{reason}"] += number
            if len(cohort_returns) != K:
                raise Table2ValidationError("A decile does not contain six active cohorts")
            decile_returns.append(statistics.fmean(cohort_returns))
            expected_slots.append(expected)
            usable_slots.append(usable)
        row: dict[str, object] = {
            "yyyymm": yyyymm_from_index(holding_month),
            "market_return": market_by_month[holding_month],
            "active_cohorts": K,
        }
        for decile, value in enumerate(decile_returns, start=1):
            row[f"p{decile}_return"] = value
            row[f"p{decile}_expected_slots"] = expected_slots[decile - 1]
            row[f"p{decile}_usable_slots"] = usable_slots[decile - 1]
        row["wml_return"] = decile_returns[9] - decile_returns[0]
        rows.append(row)
    return rows, diagnostics


def average_formation_caps(
    cohorts: dict[int, Cohort],
    evaluation_months: list[int],
    formation_caps: dict[tuple[int, int], tuple[float | None, bool]],
) -> tuple[dict[int, float], Counter[str]]:
    """Average cohort-level formation caps; source MthCap is in $thousands."""
    used_formations = sorted({
        formation_month
        for holding_month in evaluation_months
        for formation_month in range(holding_month - K, holding_month)
    })
    averages: dict[int, float] = {}
    diagnostics: Counter[str] = Counter()
    for decile in range(10):
        cohort_means: list[float] = []
        for formation_month in used_formations:
            values: list[float] = []
            for permno in cohorts[formation_month].members[decile]:
                cap, usable = formation_caps[(permno, formation_month)]
                diagnostics[f"P{decile + 1}_cap_expected"] += 1
                if not usable:
                    diagnostics[f"P{decile + 1}_cap_unusable"] += 1
                    continue
                if cap is None or not math.isfinite(float(cap)) or float(cap) <= 0:
                    raise Table2ValidationError("A usable market cap is invalid")
                values.append(float(cap))
            if not values:
                raise Table2ValidationError("A cohort-decile has no usable formation cap")
            cohort_means.append(statistics.fmean(values) / 1000.0)
        averages[decile + 1] = statistics.fmean(cohort_means)
        diagnostics[f"P{decile + 1}_formation_months"] = len(cohort_means)
    diagnostics["first_formation"] = used_formations[0]
    diagnostics["last_formation"] = used_formations[-1]
    return averages, diagnostics


def run_synthetic_tests() -> None:
    signals = [(permno, float(permno)) for permno in range(1, 21)]
    members = assign_deciles(signals)
    if members[0] != (1, 2) or members[9] != (19, 20):
        raise Table2ValidationError("Synthetic decile test failed")
    x = [-0.02, 0.00, 0.01, 0.03]
    y = [0.01 + 2.0 * value for value in x]
    alpha, beta = ols_alpha_beta(y, x)
    if not math.isclose(alpha, 0.01, abs_tol=1e-15) or not math.isclose(beta, 2.0, abs_tol=1e-15):
        raise Table2ValidationError("Synthetic beta test failed")
    value, count, excluded = equal_weight_return(
        (1, 2, 3), {1: (0.10, True, "N"), 2: (None, False, "N"), 3: (0.20, True, "N")}
    )
    if not math.isclose(value, 0.15) or count != 2 or excluded["missing_return"] != 1:
        raise Table2ValidationError("Synthetic equal-weight return test failed")


def write_csv_atomic(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise Table2ValidationError("Cannot write an empty CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)
    path.chmod(0o644)


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    os.replace(temporary, path)
    path.chmod(0o644)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared", type=Path,
        default=ROOT / "processed" / "monthly_stock_prepared.parquet",
    )
    parser.add_argument(
        "--monthly-lookup", type=Path,
        default=ROOT / "processed" / "table1_monthly_return_lookup.parquet",
    )
    parser.add_argument(
        "--monthly-raw", type=Path,
        default=ROOT / "02_Monthly_Stocks_and_Factors" / "data" / "monthly_stock.csv",
    )
    parser.add_argument(
        "--table1-monthly", type=Path,
        default=ROOT / "outputs" / "tables" / "table1_monthly_returns.csv",
    )
    parser.add_argument(
        "--table-output", type=Path,
        default=ROOT / "outputs" / "tables" / "table2_replication.csv",
    )
    parser.add_argument(
        "--monthly-output", type=Path,
        default=ROOT / "outputs" / "tables" / "table2_monthly_portfolios.csv",
    )
    parser.add_argument(
        "--report-output", type=Path,
        default=ROOT / "outputs" / "logs" / "table2_replication_report.txt",
    )
    args = parser.parse_args()
    inputs = (args.prepared, args.monthly_lookup, args.monthly_raw, args.table1_monthly)
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"Required input not found: {path}")
    signatures_before = {
        path: file_signature(path, include_hash=path != args.monthly_raw)
        for path in inputs
    }

    run_synthetic_tests()
    cap_scale_observations, cap_scale_max_deviation = validate_market_cap_scale(
        args.monthly_raw
    )
    (
        eligibility, formation_caps, market_by_month,
        returns_by_stock, returns_by_month, prepared_rows, lookup_rows,
    ) = read_inputs(args.prepared, args.monthly_lookup)
    signals, signal_diagnostics = calculate_signals(eligibility, returns_by_stock)
    cohorts, cohort_diagnostics = form_cohorts(signals)
    evaluation_months, table1_baseline = read_evaluation_months(args.table1_monthly)
    monthly_rows, holding_diagnostics = build_monthly_portfolios(
        cohorts, evaluation_months, returns_by_month, market_by_month
    )
    average_caps, cap_diagnostics = average_formation_caps(
        cohorts, evaluation_months, formation_caps
    )

    max_p1_difference = max(
        abs(float(row["p1_return"]) - table1_baseline[month][0])
        for row, month in zip(monthly_rows, evaluation_months, strict=True)
    )
    max_p10_difference = max(
        abs(float(row["p10_return"]) - table1_baseline[month][1])
        for row, month in zip(monthly_rows, evaluation_months, strict=True)
    )
    if max(max_p1_difference, max_p10_difference) > 1e-12:
        raise Table2ValidationError("Table II P1/P10 returns do not reproduce Table I")

    market = [float(row["market_return"]) for row in monthly_rows]
    betas: dict[str, float] = {}
    alphas: dict[str, float] = {}
    for decile in range(1, 11):
        portfolio = f"P{decile}"
        values = [float(row[f"p{decile}_return"]) for row in monthly_rows]
        alphas[portfolio], betas[portfolio] = ols_alpha_beta(values, market)
    wml = [float(row["wml_return"]) for row in monthly_rows]
    alphas["P10-P1"], betas["P10-P1"] = ols_alpha_beta(wml, market)
    if not math.isclose(
        betas["P10-P1"], betas["P10"] - betas["P1"], abs_tol=1e-12
    ):
        raise Table2ValidationError("P10-P1 beta does not equal the beta difference")
    if any(
        not math.isclose(
            float(row["wml_return"]),
            float(row["p10_return"]) - float(row["p1_return"]),
            abs_tol=1e-15,
        )
        for row in monthly_rows
    ):
        raise Table2ValidationError("Monthly P10-P1 identity failed")

    summary_rows: list[dict[str, object]] = []
    for portfolio in [*(f"P{decile}" for decile in range(1, 11)), "P10-P1"]:
        original_beta, original_cap = ORIGINAL[portfolio]
        cap = average_caps.get(int(portfolio[1:])) if portfolio != "P10-P1" else None
        summary_rows.append({
            "portfolio": portfolio,
            "modern_beta": f"{betas[portfolio]:.6f}",
            "modern_average_market_cap_millions": "" if cap is None else f"{cap:.6f}",
            "original_beta": f"{original_beta:.2f}",
            "original_average_market_cap_millions": "" if original_cap is None else f"{original_cap:.2f}",
            "modern_minus_original_beta": f"{betas[portfolio] - original_beta:.6f}",
        })

    signatures_after = {
        path: file_signature(path, include_hash=path != args.monthly_raw)
        for path in inputs
    }
    if signatures_before != signatures_after:
        raise Table2ValidationError("An input or Table I output changed during execution")
    write_csv_atomic(args.table_output, summary_rows)
    write_csv_atomic(args.monthly_output, monthly_rows)

    total_expected_caps = sum(
        cap_diagnostics[f"P{decile}_cap_expected"] for decile in range(1, 11)
    )
    total_unusable_caps = sum(
        cap_diagnostics[f"P{decile}_cap_unusable"] for decile in range(1, 11)
    )
    total_holding_exclusions = sum(holding_diagnostics.values())
    result_lines = [
        "Portfolio  Modern beta  Average cap ($m)  Original beta  Original cap ($m)"
    ]
    for row in summary_rows:
        result_lines.append(
            f"{row['portfolio']:>7}  {float(row['modern_beta']):>11.4f}  "
            f"{row['modern_average_market_cap_millions'] or '':>16}  "
            f"{float(row['original_beta']):>13.2f}  "
            f"{row['original_average_market_cap_millions'] or '':>17}"
        )
    report = f"""TABLE II REPLICATION REPORT

STATUS
Completed the modern-sample replication of Jegadeesh and Titman (1993) Table II.
Strategy: J=6, K=6, no skip week (Table I Panel A convention).
Evaluation period: {EXPECTED_EVALUATION['first']}-{EXPECTED_EVALUATION['last']} ({len(evaluation_months)} months).

PAPER-STATED METHOD
Stocks are ranked on six-month lagged returns into ten equal-weight deciles and held for six months. Table II reports post-ranking betas relative to the value-weighted index and average market capitalization. The original sample is January 1965-December 1989.

IMPLEMENTATION ASSUMPTIONS REQUIRING METHODOLOGICAL REVIEW
1. The paper does not specify the beta regression equation for Table II. This implementation estimates R_pt = alpha_p + beta_p*R_mt + error using monthly total returns and an intercept. R_mt is the supplied CRSP value-weighted return including distributions. No risk-free rate is subtracted.
2. The paper does not specify the market-cap measurement date or averaging order. This implementation uses end-of-formation-month MthCap, equal weights stocks within each cohort-decile, then equal weights formation cohorts. MthCap is converted from thousands of dollars to millions of dollars.
3. Exact formation-return ties are broken by ascending PERMNO. The original paper does not state a tie rule.
4. The modern evaluation period is kept identical to the validated Table I common period so Table I and Table II describe the same return sample.

RESULTS
{chr(10).join(result_lines)}

DATA AND PORTFOLIO CONSTRUCTION
Stage 2 input: {prepared_rows:,} rows, {EXPECTED_STAGE2['stocks']:,} stocks, {EXPECTED_STAGE2['months']} months; unique PERMNO-month keys confirmed.
Unfiltered holding-return lookup: {lookup_rows:,} stock-months.
Valid J=6 stock-formation signals: {signal_diagnostics['valid']:,}; rejected for missing formation month: {signal_diagnostics['missing_month']:,}; rejected for unusable formation return: {signal_diagnostics['unusable_return']:,}.
Formation cohorts available: {cohort_diagnostics['formation_months']:,}; exact tie groups: {cohort_diagnostics['tie_groups']:,}.
Market-cap cohorts used: {yyyymm_from_index(cap_diagnostics['first_formation'])}-{yyyymm_from_index(cap_diagnostics['last_formation'])}.
Usable formation caps: {total_expected_caps-total_unusable_caps:,}/{total_expected_caps:,} ({(total_expected_caps-total_unusable_caps)/total_expected_caps:.2%}); missing or nonpositive caps affect only the descriptive cap statistic.
Market-cap scale check: MthCap equaled abs(MthPrc)*ShrOut within {cap_scale_max_deviation:.2e} in {cap_scale_observations:,} deterministic source rows. Because the supplied dictionary defines ShrOut in thousands, MthCap is treated as thousands of dollars.
Holding-component exclusions under the unchanged Table I convention: {total_holding_exclusions:,}. Missing returns are not set to zero; each affected cohort-decile is reweighted over its usable members. Complete delisting-adjusted returns remain included.

VALIDATION
PASS - synthetic decile, equal-weight return, missing-return, and known-beta tests.
PASS - all ten deciles are exhaustive, mutually exclusive, ordered by signal, and differ in size by at most one stock in every formation cohort.
PASS - every reported month contains six active cohorts aged one through six months; no future return enters formation.
PASS - supplied vwretd is unique within every month and available in all 288 evaluation months.
PASS - reconstructed P1 and P10 returns match validated Table I monthly returns; maximum absolute differences are {max_p1_difference:.3e} and {max_p10_difference:.3e}.
PASS - monthly P10-P1 equals P10 minus P1, and its OLS beta equals beta(P10)-beta(P1) within 1e-12.
PASS - all inputs and existing Table I outputs were unchanged during execution.

INTERPRETATION LIMITS
Modern capitalization is nominal and is not directly comparable to the paper's 1965-1989 dollar values without an explicit inflation adjustment. Table II does not report beta standard errors, and this replication follows that presentation. The beta and market-cap timing assumptions above should be approved before Table II is treated as methodologically final.

OUTPUTS
{args.table_output}
{args.monthly_output}
{args.report_output}
"""
    write_text_atomic(args.report_output, report)
    print(report)


if __name__ == "__main__":
    main()
