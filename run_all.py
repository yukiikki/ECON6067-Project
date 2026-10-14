"""Run the complete ECON6067 replication workflow in dependency order.

The default workflow creates the inspection report, prepared monthly panel,
Tables I-III, and the Independent Extension outputs. The preliminary J=6/K=6
development script and the independent Table I validation are optional because
they duplicate substantial work and are not required to create the main tables.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MINIMUM_PYTHON = (3, 10)
EXPECTED_PACKAGES = {
    "duckdb": "1.5.6",
    "pyarrow": "22.0.0",
}


class WorkflowError(RuntimeError):
    """The workflow cannot start or a stage did not complete correctly."""


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    script: Path
    outputs: tuple[Path, ...]


REQUIRED_INPUTS = (
    ROOT / "02_Monthly_Stocks_and_Factors" / "data" / "monthly_stock.csv",
    ROOT / "02_Monthly_Stocks_and_Factors" / "data" / "F-F factors and RF.csv",
    ROOT / "03_Daily_Stocks" / "data" / "daily_stock.csv",
    ROOT / "external_data" / "F-F_Momentum_Factor 2.csv",
)

MAIN_STAGES = (
    Stage(
        "inspection",
        "Data inspection",
        ROOT / "src" / "01_data_inspection.py",
        (ROOT / "outputs" / "logs" / "inspection_report.txt",),
    ),
    Stage(
        "preparation",
        "Sample construction",
        ROOT / "src" / "02_data_preparation.py",
        (
            ROOT / "processed" / "monthly_stock_prepared.parquet",
            ROOT / "outputs" / "logs" / "sample_construction_report.txt",
        ),
    ),
    Stage(
        "table1",
        "Table I replication",
        ROOT / "src" / "04_table1_replication.py",
        (
            ROOT / "processed" / "table1_monthly_return_lookup.parquet",
            ROOT / "processed" / "table1_daily_cumulative.parquet",
            ROOT / "outputs" / "tables" / "table1_replication.csv",
            ROOT / "outputs" / "tables" / "table1_monthly_returns.csv",
            ROOT / "outputs" / "logs" / "table1_replication_report.txt",
        ),
    ),
    Stage(
        "table2",
        "Table II replication",
        ROOT / "src" / "05_table2_replication.py",
        (
            ROOT / "outputs" / "tables" / "table2_replication.csv",
            ROOT / "outputs" / "tables" / "table2_monthly_portfolios.csv",
            ROOT / "outputs" / "logs" / "table2_replication_report.txt",
        ),
    ),
    Stage(
        "table3",
        "Table III replication",
        ROOT / "src" / "06_table3_replication.py",
        (
            ROOT / "processed" / "table3_daily_market.parquet",
            ROOT / "processed" / "table3_scholes_williams_betas.parquet",
            ROOT / "outputs" / "tables" / "table3_replication.csv",
            ROOT / "outputs" / "tables" / "table3_monthly_portfolios.csv",
            ROOT / "outputs" / "logs" / "table3_replication_report.txt",
        ),
    ),
    Stage(
        "extension",
        "Independent Extension",
        ROOT / "src" / "07_extension.py",
        (
            ROOT / "outputs" / "tables" / "independent_extension_descriptive.csv",
            ROOT / "outputs" / "tables" / "independent_extension_regression.csv",
            ROOT / "outputs" / "tables" / "independent_extension_monthly.csv",
            ROOT / "outputs" / "figures" / "independent_extension_cumulative_returns.svg",
            ROOT / "outputs" / "logs" / "independent_extension_report.txt",
        ),
    ),
)

DEVELOPMENT_STAGE = Stage(
    "development-j6k6",
    "Preliminary Panel A J=6/K=6 development result",
    ROOT / "src" / "03_momentum_j6_k6.py",
    (
        ROOT / "outputs" / "tables" / "table1_panel_a_j6_k6.csv",
        ROOT / "outputs" / "tables" / "table1_panel_a_j6_k6_monthly.csv",
        ROOT / "outputs" / "logs" / "momentum_j6_k6_report.txt",
    ),
)

VALIDATION_STAGE = Stage(
    "table1-validation",
    "Independent Table I validation",
    ROOT / "src" / "validation" / "validate_table1.py",
    (ROOT / "outputs" / "logs" / "table1_validation.md",),
)


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_plan(include_development: bool, include_validation: bool) -> list[Stage]:
    plan: list[Stage] = []
    for stage in MAIN_STAGES:
        plan.append(stage)
        if stage.key == "preparation" and include_development:
            plan.append(DEVELOPMENT_STAGE)
        if stage.key == "table1" and include_validation:
            plan.append(VALIDATION_STAGE)
    return plan


def preflight(plan: list[Stage]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    if sys.version_info < MINIMUM_PYTHON:
        errors.append(
            f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer is required; "
            f"found {sys.version.split()[0]}."
        )
    for package, expected in EXPECTED_PACKAGES.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            errors.append(
                f"Missing package {package}=={expected}; install requirements.txt."
            )
            continue
        if installed != expected:
            warnings.append(
                f"{package} version is {installed}; requirements.txt pins {expected}."
            )
    for path in REQUIRED_INPUTS:
        if not path.is_file():
            errors.append(f"Missing required input: {relative(path)}")
        elif path.stat().st_size == 0:
            errors.append(f"Required input is empty: {relative(path)}")
    for stage in plan:
        if not stage.script.is_file():
            errors.append(f"Missing workflow script: {relative(stage.script)}")
    if warnings:
        print("Preflight warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        raise WorkflowError("Preflight failed:\n  - " + "\n  - ".join(errors))
    return warnings


def print_plan(plan: list[Stage]) -> None:
    print("Workflow plan:")
    for number, stage in enumerate(plan, start=1):
        print(f"  {number}. {stage.name}: {relative(stage.script)}")


def verify_outputs(stage: Stage) -> None:
    missing = [
        relative(path)
        for path in stage.outputs
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise WorkflowError(
            f"{stage.name} finished without expected outputs:\n  - "
            + "\n  - ".join(missing)
        )


def run_stage(stage: Stage, position: int, total: int) -> float:
    print(f"\n[{position}/{total}] {stage.name}", flush=True)
    print(f"Running {relative(stage.script)}", flush=True)
    started = time.monotonic()
    try:
        subprocess.run(
            [sys.executable, str(stage.script)],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise WorkflowError(
            f"{stage.name} failed with exit code {exc.returncode}."
        ) from exc
    verify_outputs(stage)
    elapsed = time.monotonic() - started
    print(f"Completed {stage.name} in {elapsed / 60:.1f} minutes.", flush=True)
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate inputs, scripts, Python, and dependencies without running analyses",
    )
    parser.add_argument(
        "--include-development-j6k6",
        action="store_true",
        help="also run the preliminary J=6/K=6 script retained for transparency",
    )
    parser.add_argument(
        "--include-table1-validation",
        action="store_true",
        help="also rerun the independent Table I validation",
    )
    args = parser.parse_args()

    plan = build_plan(
        include_development=args.include_development_j6k6,
        include_validation=args.include_table1_validation,
    )
    print(f"Project root: {ROOT}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print_plan(plan)
    preflight(plan)
    print("Preflight: PASS")
    if args.check:
        print("Check-only mode: no analysis scripts were run.")
        return

    for directory in (
        ROOT / "processed",
        ROOT / "outputs" / "tables",
        ROOT / "outputs" / "figures",
        ROOT / "outputs" / "logs",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    timings: list[tuple[str, float]] = []
    try:
        for position, stage in enumerate(plan, start=1):
            timings.append((stage.name, run_stage(stage, position, len(plan))))
    except KeyboardInterrupt as exc:
        raise SystemExit("Workflow interrupted by user.") from exc

    total = time.monotonic() - started
    print("\nWorkflow completed successfully.")
    for name, elapsed in timings:
        print(f"  - {name}: {elapsed / 60:.1f} minutes")
    print(f"Total elapsed time: {total / 60:.1f} minutes")
    print("Main outputs are in outputs/tables, outputs/figures, and outputs/logs.")


if __name__ == "__main__":
    try:
        main()
    except WorkflowError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
