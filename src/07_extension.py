"""Compare the replicated J=6, K=6 WML return with French's Mom factor.

The local Kenneth French CSV contains prose, monthly observations, and an
annual section. Only rows whose first field is exactly six digits (YYYYMM)
are read. Source files and Tables I-III remain read-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import math
import os
import re
import statistics
from pathlib import Path
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parents[1]
FIRST_MONTH = 200201
LAST_MONTH = 202512
EXPECTED_MONTHS = 288
MISSING_MARKERS = {-99.99, -999.0, -999.99}


class ExtensionValidationError(RuntimeError):
    """An input, merge, calculation, or output validation failed."""


def month_index(yyyymm: int) -> int:
    year, month = divmod(int(yyyymm), 100)
    if year < 1900 or not 1 <= month <= 12:
        raise ExtensionValidationError(f"Invalid YYYYMM value: {yyyymm}")
    return year * 12 + month - 1


def yyyymm_from_index(value: int) -> int:
    year, zero_month = divmod(value, 12)
    return year * 100 + zero_month + 1


def file_signature(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return stat.st_size, stat.st_mtime_ns, hasher.hexdigest()


def read_french_mom(path: Path):
    monthly: dict[int, float] = {}
    raw_percent: dict[int, float] = {}
    diagnostics = {
        "file_rows": 0, "six_digit_rows": 0, "missing_monthly_rows": 0,
        "ignored_rows": 0,
    }
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream):
            diagnostics["file_rows"] += 1
            if not row:
                diagnostics["ignored_rows"] += 1
                continue
            key = row[0].strip()
            if re.fullmatch(r"\d{6}", key) is None:
                diagnostics["ignored_rows"] += 1
                continue
            if len(row) < 2:
                raise ExtensionValidationError(f"Monthly French row lacks Mom: {row}")
            yyyymm = int(key)
            month_index(yyyymm)
            if yyyymm in monthly:
                raise ExtensionValidationError(f"Duplicate French month: {yyyymm}")
            try:
                value = float(row[1].strip())
            except ValueError as exc:
                raise ExtensionValidationError(
                    f"Invalid Mom value in {yyyymm}: {row[1]!r}"
                ) from exc
            diagnostics["six_digit_rows"] += 1
            raw_percent[yyyymm] = value
            if value in MISSING_MARKERS:
                monthly[yyyymm] = math.nan
                diagnostics["missing_monthly_rows"] += 1
            else:
                monthly[yyyymm] = value / 100.0
    if not monthly:
        raise ExtensionValidationError("No six-digit monthly French observations found")
    ordered = sorted(monthly)
    indexes = [month_index(value) for value in ordered]
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ExtensionValidationError("French monthly series has unexpected date gaps")
    for yyyymm, value in monthly.items():
        if math.isfinite(value) and not math.isclose(
            value * 100.0, raw_percent[yyyymm], abs_tol=1e-12
        ):
            raise ExtensionValidationError("French percentage-to-decimal conversion failed")
    return monthly, diagnostics, min(monthly), max(monthly)


def read_table1_wml(path: Path):
    monthly: dict[int, float] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"panel", "J", "K", "yyyymm", "wml_return", "p1_return", "p10_return"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ExtensionValidationError("Table I monthly output lacks required columns")
        for row in reader:
            if row["panel"] != "A" or int(row["J"]) != 6 or int(row["K"]) != 6:
                continue
            yyyymm = int(row["yyyymm"])
            value = float(row["wml_return"])
            identity = float(row["p10_return"]) - float(row["p1_return"])
            if not math.isclose(value, identity, abs_tol=1e-15):
                raise ExtensionValidationError(f"Table I WML identity failed in {yyyymm}")
            if yyyymm in monthly or not math.isfinite(value):
                raise ExtensionValidationError("Duplicate or invalid Table I WML observation")
            monthly[yyyymm] = value
    ordered = sorted(monthly)
    if (
        len(ordered) != EXPECTED_MONTHS
        or ordered[0] != FIRST_MONTH
        or ordered[-1] != LAST_MONTH
        or [month_index(value) for value in ordered]
        != list(range(month_index(FIRST_MONTH), month_index(LAST_MONTH) + 1))
    ):
        raise ExtensionValidationError("Unexpected Table I J=6, K=6 evaluation period")
    return monthly


def merge_series(wml: dict[int, float], mom: dict[int, float]):
    common = sorted(
        month for month in set(wml).intersection(mom)
        if FIRST_MONTH <= month <= LAST_MONTH and math.isfinite(mom[month])
    )
    if len(common) != EXPECTED_MONTHS or common[0] != FIRST_MONTH or common[-1] != LAST_MONTH:
        raise ExtensionValidationError(
            f"Expected {EXPECTED_MONTHS} common months, found {len(common)}"
        )
    rows: list[dict[str, object]] = []
    wml_wealth = mom_wealth = 1.0
    for yyyymm in common:
        wml_return = wml[yyyymm]
        mom_return = mom[yyyymm]
        if wml_return <= -1 or mom_return <= -1:
            raise ExtensionValidationError("A monthly return cannot be compounded from $1")
        wml_wealth *= 1.0 + wml_return
        mom_wealth *= 1.0 + mom_return
        rows.append({
            "yyyymm": yyyymm,
            "month": f"{yyyymm // 100:04d}-{yyyymm % 100:02d}-01",
            "replicated_wml_return": wml_return,
            "french_mom_return": mom_return,
            "replicated_wml_cumulative_return": wml_wealth - 1.0,
            "french_mom_cumulative_return": mom_wealth - 1.0,
        })
    return rows


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 3:
        raise ExtensionValidationError("Invalid correlation inputs")
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (x-left_mean)*(y-right_mean) for x, y in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((x-left_mean)**2 for x in left)
        * sum((y-right_mean)**2 for y in right)
    )
    if denominator <= 0:
        raise ExtensionValidationError("Correlation denominator is zero")
    return numerator / denominator


def inverse_2x2(matrix: list[list[float]]) -> list[list[float]]:
    determinant = matrix[0][0]*matrix[1][1] - matrix[0][1]*matrix[1][0]
    if abs(determinant) < 1e-18:
        raise ExtensionValidationError("Singular OLS matrix")
    return [
        [matrix[1][1]/determinant, -matrix[0][1]/determinant],
        [-matrix[1][0]/determinant, matrix[0][0]/determinant],
    ]


def beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 300
    epsilon = 3e-14
    tiny = 1e-300
    qab, qap, qam = a+b, a+1.0, a-1.0
    c = 1.0
    d = 1.0-qab*x/qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0/d
    result = d
    for iteration in range(1, maximum_iterations+1):
        twice = 2*iteration
        term = iteration*(b-iteration)*x/((qam+twice)*(a+twice))
        d = 1.0+term*d
        if abs(d) < tiny:
            d = tiny
        c = 1.0+term/c
        if abs(c) < tiny:
            c = tiny
        d = 1.0/d
        result *= d*c
        term = -(a+iteration)*(qab+iteration)*x/((a+twice)*(qap+twice))
        d = 1.0+term*d
        if abs(d) < tiny:
            d = tiny
        c = 1.0+term/c
        if abs(c) < tiny:
            c = tiny
        d = 1.0/d
        change = d*c
        result *= change
        if abs(change-1.0) < epsilon:
            return result
    raise ExtensionValidationError("Incomplete-beta calculation did not converge")


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)
        + a*math.log(x)+b*math.log1p(-x)
    )
    if x < (a+1.0)/(a+b+2.0):
        return front*beta_continued_fraction(a, b, x)/a
    return 1.0-front*beta_continued_fraction(b, a, 1.0-x)/b


def student_t_two_sided_p(t_statistic: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0 or not math.isfinite(t_statistic):
        raise ExtensionValidationError("Invalid t-statistic or degrees of freedom")
    x = degrees_of_freedom/(degrees_of_freedom+t_statistic*t_statistic)
    return regularized_beta(x, degrees_of_freedom/2.0, 0.5)


def ols_regression(y: list[float], x: list[float]):
    if len(y) != len(x) or len(y) < 3:
        raise ExtensionValidationError("Invalid OLS sample")
    n = len(y)
    x_mean = statistics.fmean(x)
    y_mean = statistics.fmean(y)
    sxx = sum((value-x_mean)**2 for value in x)
    if sxx <= 0:
        raise ExtensionValidationError("OLS factor has no variation")
    beta = sum(
        (factor-x_mean)*(value-y_mean)
        for value, factor in zip(y, x, strict=True)
    )/sxx
    alpha = y_mean-beta*x_mean
    residuals = [value-alpha-beta*factor for value, factor in zip(y, x, strict=True)]
    xtx = [[float(n), sum(x)], [sum(x), sum(value*value for value in x)]]
    xtx_inverse = inverse_2x2(xtx)
    sigma2 = sum(value*value for value in residuals)/(n-2)
    covariance = [
        [sigma2*xtx_inverse[i][j] for j in range(2)] for i in range(2)
    ]
    standard_errors = [math.sqrt(covariance[i][i]) for i in range(2)]
    if min(standard_errors) <= 0:
        raise ExtensionValidationError("OLS standard error is zero")
    alpha_t = alpha/standard_errors[0]
    beta_t = beta/standard_errors[1]
    total_sum_squares = sum((value-y_mean)**2 for value in y)
    residual_sum_squares = sum(value*value for value in residuals)
    r_squared = 1.0-residual_sum_squares/total_sum_squares
    return {
        "alpha": alpha, "beta": beta, "r_squared": r_squared,
        "residual_std": math.sqrt(sigma2),
        "alpha_se": standard_errors[0], "beta_se": standard_errors[1],
        "alpha_t": alpha_t, "beta_t": beta_t,
        "alpha_p": student_t_two_sided_p(alpha_t, n-2),
        "beta_p": student_t_two_sided_p(beta_t, n-2),
    }


def descriptive_rows(name: str, values: list[float], final_cumulative: float):
    average = statistics.fmean(values)
    standard_deviation = statistics.stdev(values)
    return [
        ("descriptive", name, "observations", len(values), "months"),
        ("descriptive", name, "mean_monthly_return", average, "decimal"),
        ("descriptive", name, "monthly_standard_deviation", standard_deviation, "decimal"),
        ("descriptive", name, "mean_t_statistic", average/(standard_deviation/math.sqrt(len(values))), "t-statistic"),
        ("descriptive", name, "median_monthly_return", statistics.median(values), "decimal"),
        ("descriptive", name, "minimum_monthly_return", min(values), "decimal"),
        ("descriptive", name, "maximum_monthly_return", max(values), "decimal"),
        ("descriptive", name, "positive_month_fraction", sum(value > 0 for value in values)/len(values), "fraction"),
        ("descriptive", name, "annualized_arithmetic_mean", 12*average, "decimal"),
        ("descriptive", name, "annualized_volatility", math.sqrt(12)*standard_deviation, "decimal"),
        ("descriptive", name, "final_cumulative_return", final_cumulative, "decimal"),
    ]


def format_percent(value: float) -> str:
    return f"{value*100:.1f}%"


def build_svg(path: Path, rows: list[dict[str, object]]) -> None:
    width, height = 1200, 700
    left, right, top, bottom = 105, 45, 90, 90
    plot_width = width-left-right
    plot_height = height-top-bottom
    wml = [0.0] + [float(row["replicated_wml_cumulative_return"]) for row in rows]
    mom = [0.0] + [float(row["french_mom_cumulative_return"]) for row in rows]
    minimum = min(0.0, *wml, *mom)
    maximum = max(0.0, *wml, *mom)
    padding = max((maximum-minimum)*.08, .05)
    y_min, y_max = minimum-padding, maximum+padding

    def x_coordinate(position: int) -> float:
        return left + position/(len(rows))*plot_width

    def y_coordinate(value: float) -> float:
        return top + (y_max-value)/(y_max-y_min)*plot_height

    def points(values: list[float]) -> str:
        return " ".join(
            f"{x_coordinate(position):.2f},{y_coordinate(value):.2f}"
            for position, value in enumerate(values)
        )

    grid_lines: list[str] = []
    ticks = 6
    for number in range(ticks+1):
        value = y_min + number*(y_max-y_min)/ticks
        y = y_coordinate(value)
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" '
            'stroke="#d9e1e8" stroke-width="1"/>'
            f'<text x="{left-14}" y="{y+5:.2f}" text-anchor="end" '
            f'font-size="15" fill="#465664">{html.escape(format_percent(value))}</text>'
        )
    x_labels: list[str] = []
    label_years = (2002, 2006, 2010, 2014, 2018, 2022, 2025)
    month_positions = {int(row["yyyymm"]): index+1 for index, row in enumerate(rows)}
    for year in label_years:
        yyyymm = year*100+1
        if yyyymm not in month_positions:
            yyyymm = year*100+12
        position = month_positions[yyyymm]
        x = x_coordinate(position)
        x_labels.append(
            f'<line x1="{x:.2f}" y1="{height-bottom}" x2="{x:.2f}" y2="{height-bottom+6}" '
            'stroke="#465664" stroke-width="1"/>'
            f'<text x="{x:.2f}" y="{height-bottom+30}" text-anchor="middle" '
            f'font-size="15" fill="#465664">{year}</text>'
        )
    zero_line = ""
    if y_min <= 0 <= y_max:
        y = y_coordinate(0)
        zero_line = (
            f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" '
            'stroke="#7c8b96" stroke-width="1.4"/>'
        )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<text x="{left}" y="40" font-family="Arial, Helvetica, sans-serif" font-size="27" font-weight="700" fill="#172b3a">Cumulative return: replicated 6/6 WML and French Mom</text>
<text x="{left}" y="68" font-family="Arial, Helvetica, sans-serif" font-size="16" fill="#526471">Monthly compounding, common sample January 2002-December 2025</text>
<g font-family="Arial, Helvetica, sans-serif">
{''.join(grid_lines)}
{zero_line}
<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#465664" stroke-width="1.2"/>
<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#465664" stroke-width="1.2"/>
{''.join(x_labels)}
<polyline points="{points(wml)}" fill="none" stroke="#1f77b4" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
<polyline points="{points(mom)}" fill="none" stroke="#d97706" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
<line x1="{width-390}" y1="45" x2="{width-350}" y2="45" stroke="#1f77b4" stroke-width="4"/>
<text x="{width-340}" y="50" font-size="15" fill="#263b49">Replicated 6/6 WML</text>
<line x1="{width-190}" y1="45" x2="{width-150}" y2="45" stroke="#d97706" stroke-width="4"/>
<text x="{width-140}" y="50" font-size="15" fill="#263b49">French Mom</text>
<text transform="translate(28 {top+plot_height/2}) rotate(-90)" text-anchor="middle" font-size="16" fill="#465664">Cumulative return</text>
<text x="{left+plot_width/2}" y="{height-24}" text-anchor="middle" font-size="16" fill="#465664">Month</text>
</g>
</svg>
'''
    write_text_atomic(path, svg)


def write_csv_atomic(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ExtensionValidationError("Cannot write an empty CSV")
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


def run_synthetic_tests() -> None:
    x = [-.02, 0.0, .01, .03, .04, -.01, .02, .05, -.03, .06]
    noise = [.001, -.001, .002, -.002, .001, -.001, .002, -.002, .001, -.001]
    y = [.01+1.5*factor+error for factor, error in zip(x, noise, strict=True)]
    result = ols_regression(y, x)
    if abs(result["alpha"]-.01) > .002 or abs(result["beta"]-1.5) > .1:
        raise ExtensionValidationError("Synthetic OLS test failed")
    if not math.isclose(correlation(x, x), 1.0, abs_tol=1e-15):
        raise ExtensionValidationError("Synthetic correlation test failed")
    if not math.isclose(student_t_two_sided_p(0.0, 10), 1.0, abs_tol=1e-14):
        raise ExtensionValidationError("Synthetic Student-t p-value test failed")
    if not math.isclose(student_t_two_sided_p(1.0, 1), 0.5, abs_tol=1e-12):
        raise ExtensionValidationError("Known Student-t p-value test failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--french-mom", type=Path,
        default=ROOT/"external_data"/"F-F_Momentum_Factor 2.csv",
    )
    parser.add_argument(
        "--table1-monthly", type=Path,
        default=ROOT/"outputs"/"tables"/"table1_monthly_returns.csv",
    )
    parser.add_argument(
        "--descriptive-output", type=Path,
        default=ROOT/"outputs"/"tables"/"independent_extension_descriptive.csv",
    )
    parser.add_argument(
        "--regression-output", type=Path,
        default=ROOT/"outputs"/"tables"/"independent_extension_regression.csv",
    )
    parser.add_argument(
        "--monthly-output", type=Path,
        default=ROOT/"outputs"/"tables"/"independent_extension_monthly.csv",
    )
    parser.add_argument(
        "--figure-output", type=Path,
        default=ROOT/"outputs"/"figures"/"independent_extension_cumulative_returns.svg",
    )
    parser.add_argument(
        "--report-output", type=Path,
        default=ROOT/"outputs"/"logs"/"independent_extension_report.txt",
    )
    args = parser.parse_args()
    inputs = (args.french_mom, args.table1_monthly)
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"Required input not found: {path}")
    signatures_before = {path: file_signature(path) for path in inputs}
    run_synthetic_tests()
    french, french_diagnostics, french_first, french_last = read_french_mom(args.french_mom)
    wml = read_table1_wml(args.table1_monthly)
    monthly_rows = merge_series(wml, french)
    wml_values = [float(row["replicated_wml_return"]) for row in monthly_rows]
    mom_values = [float(row["french_mom_return"]) for row in monthly_rows]
    pearson = correlation(wml_values, mom_values)
    regression = ols_regression(wml_values, mom_values)
    if not math.isclose(regression["r_squared"], pearson**2, abs_tol=1e-12):
        raise ExtensionValidationError("Simple-regression R-squared/correlation identity failed")

    summary_tuples = [
        *descriptive_rows(
            "replicated_wml", wml_values,
            float(monthly_rows[-1]["replicated_wml_cumulative_return"]),
        ),
        *descriptive_rows(
            "french_mom", mom_values,
            float(monthly_rows[-1]["french_mom_cumulative_return"]),
        ),
        ("correlation", "replicated_wml_vs_french_mom", "pearson_correlation", pearson, "correlation"),
    ]
    descriptive_output_rows = [{
        "analysis": analysis, "series_or_term": series, "statistic": statistic,
        "estimate": f"{float(estimate):.10f}", "units": units,
    } for analysis, series, statistic, estimate, units in summary_tuples]
    regression_rows = []
    for term in ("alpha", "beta"):
        units = "decimal monthly return" if term == "alpha" else "factor loading"
        regression_rows.append({
            "term_or_statistic": term,
            "estimate": f"{regression[term]:.10f}", "units": units,
            "standard_error": f"{regression[f'{term}_se']:.10f}",
            "t_statistic": f"{regression[f'{term}_t']:.6f}",
            "p_value": f"{regression[f'{term}_p']:.10g}",
        })
    regression_rows.extend([
        {
            "term_or_statistic": "r_squared",
            "estimate": f"{regression['r_squared']:.10f}",
            "units": "fraction", "standard_error": "", "t_statistic": "",
            "p_value": "",
        },
        {
            "term_or_statistic": "residual_standard_deviation",
            "estimate": f"{regression['residual_std']:.10f}", "units": "decimal monthly return",
            "standard_error": "", "t_statistic": "",
            "p_value": "",
        },
    ])
    signatures_after = {path: file_signature(path) for path in inputs}
    if signatures_before != signatures_after:
        raise ExtensionValidationError("A source or Table I output changed during execution")
    write_csv_atomic(args.descriptive_output, descriptive_output_rows)
    write_csv_atomic(args.regression_output, regression_rows)
    write_csv_atomic(args.monthly_output, monthly_rows)
    build_svg(args.figure_output, monthly_rows)

    wml_mean, wml_sd = statistics.fmean(wml_values), statistics.stdev(wml_values)
    mom_mean, mom_sd = statistics.fmean(mom_values), statistics.stdev(mom_values)
    wml_final = float(monthly_rows[-1]["replicated_wml_cumulative_return"])
    mom_final = float(monthly_rows[-1]["french_mom_cumulative_return"])
    report = f"""INDEPENDENT EXTENSION: REPLICATED WML VERSUS KENNETH FRENCH MOM

STATUS
Completed the local-file comparison using Table I Panel A, J=6, K=6 WML.
Common sample: {FIRST_MONTH}-{LAST_MONTH} ({len(monthly_rows)} months).

FRENCH FILE PARSING
Source: {args.french_mom}
Only rows with an exact six-digit YYYYMM first field were retained. Prose, headers, blank rows, and four-digit annual factors were ignored.
Monthly observations found: {french_diagnostics['six_digit_rows']:,}, spanning {french_first}-{french_last}.
French missing markers (-99.99, -999, and -999.99) were converted to NaN before merging. Missing monthly rows in the full French series: {french_diagnostics['missing_monthly_rows']:,}; missing rows in the 2002-2025 common sample: 0.
French Mom was converted from percent to decimal by dividing by 100. Table I returns were already decimals.

DESCRIPTIVE STATISTICS
                              Replicated WML      French Mom
Mean monthly return              {wml_mean*100:>9.4f}%       {mom_mean*100:>9.4f}%
Monthly standard deviation       {wml_sd*100:>9.4f}%       {mom_sd*100:>9.4f}%
Mean t-statistic                 {wml_mean/(wml_sd/math.sqrt(len(wml_values))):>9.4f}        {mom_mean/(mom_sd/math.sqrt(len(mom_values))):>9.4f}
Positive-month fraction          {sum(v>0 for v in wml_values)/len(wml_values):>9.2%}        {sum(v>0 for v in mom_values)/len(mom_values):>9.2%}
Final cumulative return          {wml_final*100:>9.2f}%       {mom_final*100:>9.2f}%

CORRELATION AND OLS
Pearson monthly-return correlation: {pearson:.6f}.
Regression: replicated_WML_t = alpha + beta*French_Mom_t + error_t.
Alpha: {regression['alpha']*100:.4f}% per month; t={regression['alpha_t']:.4f}; two-sided p={regression['alpha_p']:.4f}.
Beta on French Mom: {regression['beta']:.6f}; t={regression['beta_t']:.4f}; two-sided p={regression['beta_p']:.4g}.
R-squared: {regression['r_squared']:.4%}.

ECONOMIC INTERPRETATION
The correlation and beta measure how much the locally replicated 6/6 WML strategy moves with French Mom. They are not expected to equal one because French Mom uses prior returns from months -12 to -2, value-weighted portfolios, NYSE breakpoints, and NYSE/AMEX/NASDAQ stocks, whereas the replication uses six-month formation returns, equal weights, NYSE/NYSE American stocks, and six overlapping holding cohorts.
The regression alpha is the average component of replicated WML not explained by contemporaneous French Mom exposure. It is not a CAPM alpha and does not by itself establish a distinct anomaly. Conventional OLS standard errors are reported for a transparent baseline; serial dependence from overlapping six-month cohorts remains a limitation.

VALIDATION
PASS - exactly {EXPECTED_MONTHS} consecutive common months from January 2002 through December 2025.
PASS - French percentage-to-decimal conversion was checked by round trip to the source values.
PASS - Table I WML equals P10 minus P1 in every selected month.
PASS - simple-regression R-squared equals squared Pearson correlation within 1e-12.
PASS - cumulative series use monthly compounding and begin from one dollar of notional wealth.
PASS - the French source CSV and all Table I-III results were unchanged.

OUTPUTS
{args.descriptive_output}
{args.regression_output}
{args.monthly_output}
{args.figure_output}
{args.report_output}
"""
    write_text_atomic(args.report_output, report)
    print(report)


if __name__ == "__main__":
    main()

