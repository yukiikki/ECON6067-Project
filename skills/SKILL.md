---
name: momentum-replication
description: Reproduce Jegadeesh & Titman (1993) Tables I–III and the Independent Extension through the existing ECON6067 run_all.py workflow. Use for replication runs, reproducibility checks, and workflow troubleshooting.
---

# Momentum Replication

## Purpose and scope

Use the existing workflow to generate Tables I–III and the Independent Extension: descriptive statistics, correlation, OLS regression, and cumulative returns comparing replicated six-month formation/six-month holding winner-minus-loser (WML) returns with the Kenneth French monthly Momentum Factor (Mom).

The instructor's [GitHub example README](https://github.com/Jeff7c/ChenJianfei_3030135471) provides a reference for a single execution entry point and a reusable Skill. Follow this project's actual layout: `run_all.py` at the project root, analysis scripts in `src/`, and intermediate data in `processed/`. Do not move files into `code/` or reorganize data directories to match the example.

- Reuse existing scripts; do not reimplement data preparation, portfolio construction, regressions, or statistical validation.
- Do not modify existing Python calculation methods, raw CSV files, completed research results, or Git history. Do not commit, push, reset, or rebase.
- A full replication run regenerates intermediate files, result tables, figures, and stage reports. For checks or explanations, use `--check` and inspect existing outputs read-only. Run the full workflow only when the user requests execution of the replication.
- Raw sample coverage is 2000–2025; the main evaluation period is January 2002–December 2025. The paper uses 1965–1989. Matching the paper's numerical estimates is not an acceptance criterion; do not adjust algorithms to force agreement.

## Project root and required inputs

This file is `skills/SKILL.md`; the project root is two directory levels above the file. Change to that root and confirm that `run_all.py`, `requirements.txt`, and `src/` exist. All commands and paths below are relative to the project root. Do not execute these commands from inside `skills/`.

Read `README.md` and the `README.md` and `DATA_DICTIONARY.md` files in both course data directories. All four inputs must exist and be nonempty:

| Input path | Purpose |
| --- | --- |
| `02_Monthly_Stocks_and_Factors/data/monthly_stock.csv` | CRSP monthly returns, classifications, capitalization, and sample construction |
| `02_Monthly_Stocks_and_Factors/data/F-F factors and RF.csv` | Monthly factors and risk-free returns |
| `03_Daily_Stocks/data/daily_stock.csv` | Table I Panel B daily signals and Table III beta estimation |
| `external_data/F-F_Momentum_Factor 2.csv` | Local French Mom snapshot for the Independent Extension |

Obtain packages 02 and 03 through the authorized course distribution, extract them at the project root, and preserve filenames. The monthly package is approximately 1.24 GB and the daily package approximately 28 GB. Allow at least 40 GB of free space as recommended in the project README, and monitor additional cache and temporary-file requirements. Do not load the entire daily CSV into memory or upload restricted course data.

Use the existing French snapshot; do not automatically download a replacement, because historical revisions may change results. Existing `src/07_extension.py` extracts six-digit `YYYYMM` monthly rows, converts percent returns to decimals, and handles missing markers. Do not manually clean the source file.

## Python environment

Python 3.10 or newer is required; the project was tested with Python 3.13. `requirements.txt` pins `duckdb==1.5.6` and `pyarrow==22.0.0`. Other imports use the standard library. The existing script generates SVG directly; Matplotlib is unnecessary.

On macOS/Linux, run from the project root. Activate an existing `.venv`; create it only if absent:

```sh
# First-time setup only, when .venv does not exist
python3 -m venv .venv
# Activate before use
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

Skip installation if the environment already has the required dependencies. You can substitute `.venv/bin/python` for `python` in the commands below to avoid interpreter mismatches. On Windows PowerShell, create the environment with `py -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1`; subsequent commands are the same.

## Execution

1. Run the read-only preflight. It checks Python, dependencies, the four inputs, and workflow scripts without executing analyses or rewriting outputs:

   ```sh
   python run_all.py --check
   ```

2. When the user requests full replication and preflight passes, run the main entry point:

   ```sh
   python run_all.py
   ```

   The sequence is `src/01_data_inspection.py` → `src/02_data_preparation.py` → `src/04_table1_replication.py` → `src/05_table2_replication.py` → `src/06_table3_replication.py` → `src/07_extension.py`. The entry point uses the active Python interpreter, sets each stage's working directory to the project root, verifies that expected outputs are nonempty, and stops at the first failure.

3. To run full replication with the existing independent Table I validation:

   ```sh
   python run_all.py --check --include-table1-validation
   python run_all.py --include-table1-validation
   ```

   This calls existing `src/validation/validate_table1.py` after Table I and before Table II. It runs the full workflow with validation, rather than validation alone. Validation reads the large daily source file and updates `outputs/logs/table1_validation.md`.

`python run_all.py --include-development-j6k6` adds the existing development stage `src/03_momentum_j6_k6.py`; it is normally unnecessary and does not replace the complete Table I. Both optional flags can be combined. Use `python run_all.py --help` to confirm available options; do not invent `--stage`, `--resume`, or global output-path flags.

## Output locations

The full workflow creates the required output directories and regenerates existing files with the same names.

| Artifact | Saved paths |
| --- | --- |
| Table I summary and monthly returns | `outputs/tables/table1_replication.csv`; `outputs/tables/table1_monthly_returns.csv` |
| Table II summary and monthly portfolios | `outputs/tables/table2_replication.csv`; `outputs/tables/table2_monthly_portfolios.csv` |
| Table III summary and monthly portfolios | `outputs/tables/table3_replication.csv`; `outputs/tables/table3_monthly_portfolios.csv` |
| Extension descriptive statistics, regression, and monthly series | `outputs/tables/independent_extension_descriptive.csv`; `outputs/tables/independent_extension_regression.csv`; `outputs/tables/independent_extension_monthly.csv` |
| Extension cumulative-return figure | `outputs/figures/independent_extension_cumulative_returns.svg` |
| Inspection and sample reports | `outputs/logs/inspection_report.txt`; `outputs/logs/sample_construction_report.txt` |
| Tables I–III stage reports | `outputs/logs/table1_replication_report.txt`; `outputs/logs/table2_replication_report.txt`; `outputs/logs/table3_replication_report.txt` |
| Extension report | `outputs/logs/independent_extension_report.txt` |
| Optional independent validation report | `outputs/logs/table1_validation.md` |
| Optional development outputs and report | `outputs/tables/table1_panel_a_j6_k6.csv`; `outputs/tables/table1_panel_a_j6_k6_monthly.csv`; `outputs/logs/momentum_j6_k6_report.txt` |

Generated intermediate files are `processed/monthly_stock_prepared.parquet`, `processed/table1_monthly_return_lookup.parquet`, `processed/table1_daily_cumulative.parquet`, `processed/table3_daily_market.parquet`, and `processed/table3_scholes_williams_betas.parquet`.

Progress, timings, and exceptions appear in the terminal. The entry point **does not automatically save a complete console log**; the files listed above are stage reports. The full workflow's completion message is `Workflow completed successfully.`.

## Validation and delivery

- Routine check: run `python run_all.py --check` and confirm `Preflight: PASS` with exit code 0. Preflight does not validate existing numerical results or establish that the full analysis has run.
- After full execution: confirm the completion message, exit code 0, and every nonempty expected output in the selected plan. Check that reports belong to the current run; old outputs left by a failed run do not establish success.
- Reuse existing stage checks for numerical acceptance: Table I contains panels A/B with 16 J/K combinations per panel, 96 summary rows, and 9,216 monthly rows covering 288 months (200201–202512). WML equals P10 minus P1, and the active cohort count equals K. Table II P1/P10 returns must match Table I Panel A 6/6; Table III All portfolios must match Table II; the Extension's single-regressor OLS with an intercept must satisfy R² = squared correlation. Inspect the stage reports for this evidence; do not implement another analysis.
- For deeper validation, use the `--include-table1-validation` workflow above and read its PASS/WARNING findings. Preserve the meaning of `CONDITIONALLY VALIDATED`; do not report unconditional methodological validation. Existing reports document Panel B's seven-calendar-day gap, renormalization after omitting unavailable/missing or delisting returns, and conventional standard errors for overlapping portfolios. Consult the Table II/III reports for beta and capitalization timing assumptions.
- Report commands executed, whether preflight or full execution succeeded, output locations, and remaining validation warnings. For check-only work, explicitly state that empirical analyses were not rerun and preserve existing results and Git history.

## Troubleshooting

| Symptom | Diagnosis and action |
| --- | --- |
| Cannot find `run_all.py` | Confirm that the working directory is the project root, two levels above this file. The entry point is not `code/run_all.py`. |
| `Missing required input` or empty input | Check all four paths, extraction nesting, file integrity, and filenames containing spaces. Restore missing files from authorized sources; do not alter code or fabricate data to bypass checks. |
| Unsupported Python or missing duckdb/pyarrow | Activate `.venv`, check `python --version`, and install with that interpreter using `python -m pip install -r requirements.txt`. Resolve dependency-version warnings by installing the pinned versions. |
| Insufficient disk or DuckDB temporary space; terminated process | Check `df -h .`, available memory, and temporary-directory space. Stop concurrent reruns; do not delete source data or results as a troubleshooting experiment. Restore resources, then repeat preflight and the main workflow. Long initial daily-cache builds or independent validation runs are not themselves failures. |
| Nonzero stage exit or `finished without expected outputs` | Inspect the terminal traceback and corresponding stage report; an old report may not have been updated. The entry point has no resume option. After resolving environment/input issues, rerun the main entry point. Do not skip failed stages or manually populate result files. |
| Corrupt cache, changed input, or inconsistent tables | Stop and check data versions, cache provenance, and current reports. An ordinary rerun does not necessarily refresh every cache. Inspect the relevant existing script's `--help`; do not automatically delete caches or invent a global rebuild flag. Explain any required input or rebuild-strategy changes and proceed within the user's authorization. |
| French Mom parsing or monthly merge failure | Check the snapshot filename and original format, then inspect the Extension report. Do not manually remove explanatory/annual rows or change units to bypass the existing parser. |
| Estimates differ from the 1993 paper | Check sample periods and documented methodological assumptions. Differences from a modern sample do not establish a coding error; do not change calculations to force agreement. |

## Example requests

“Read `skills/SKILL.md` and use momentum-replication to check the environment and existing outputs without rerunning analyses.”

“Read `skills/SKILL.md` and use momentum-replication to reproduce Tables I–III and the Independent Extension through the existing workflow, including independent Table I validation.”

Keep this file at the user-specified project path; do not automatically install it globally. If the client does not discover it automatically, explicitly reference `skills/SKILL.md` in the request.
