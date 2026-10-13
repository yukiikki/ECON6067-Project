# ECON6067: Jegadeesh and Titman momentum replication

Current milestone: **Table I completed**. All 32 combinations in Panels A and B
have been constructed and validated for the modern sample. Tables II and III
have not been implemented.

## Inputs and access

Obtain the course data through the authorized course distribution and extract
packages 02 and 03 into this project directory, preserving their original names.
Read their README and DATA_DICTIONARY files. Restricted data are not distributed
by this repository. Do not upload raw data to public GitHub.

The Stage 1 and Stage 2 scripts read only
`02_Monthly_Stocks_and_Factors/data/monthly_stock.csv`. Stage 4 also reads
`03_Daily_Stocks/data/daily_stock.csv` for Panel B. All raw inputs remain
unchanged.

## Run in VS Code

Open this project folder in VS Code, open Terminal > New Terminal, and set up
the project environment once:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Python 3.10
or newer is required. Then run each completed stage from the project root:

```sh
python src/01_data_inspection.py
python src/02_data_preparation.py
python src/03_momentum_j6_k6.py
python src/04_table1_replication.py
```

All scripts resolve default paths from their own locations.

The 1.2 GB CSV is streamed in records and inserted into temporary SQLite storage
in batches of 50,000. Exact duplicate checks cover the whole file, including
batch boundaries. Exact percentiles use disk-backed SQL indexes. Allow temporary
disk space for the database and indexes; runtime depends on the machine.
Stock identifiers and distinct stock/month sets are retained in memory, not the
full dataset. Temporary files are removed automatically after the audit.

## Inspection output

The script generates one file: `outputs/logs/inspection_report.txt`.
It contains dimensions, date coverage, duplicate and quality-flag counts,
all column types and missingness, descriptive statistics, classification counts,
and the complete monthly stock-count series. Console output is limited to
progress and a short summary. No separate CSV, JSON or console log is needed.
Rerunning replaces this report; it does not modify raw data.

## Stage 2 sample construction

The approved build is run with:

```sh
python src/02_data_preparation.py
```

The script streams the raw CSV, validates and resolves duplicates, applies the
approved exchange/common-stock rules, and creates:

- `processed/monthly_stock_prepared.parquet`
- `outputs/logs/sample_construction_report.txt`

The Parquet output has 520,438 unique stock-months, 4,669 PERMNOs and 312
months. Missing and extreme observations are preserved with explicit usability
flags. Rerunning atomically replaces the generated dataset after all validation
checks pass. It does not calculate portfolio returns.

## Stage 3: Table I, Panel A, J=6/K=6

Run the first portfolio strategy with:

```sh
python src/03_momentum_j6_k6.py
```

The script first runs small synthetic tests, validates the Stage 2 dimensions
and keys, and then forms monthly equal-weighted deciles from consecutive,
usable six-month return histories. Formation month `t` uses months `t-5`
through `t`; holding begins in `t+1`. It creates:

- `outputs/tables/table1_panel_a_j6_k6.csv`
- `outputs/tables/table1_panel_a_j6_k6_monthly.csv`
- `outputs/logs/momentum_j6_k6_report.txt`

The reported series uses only months with all six overlapping cohorts. Missing
holding returns are never replaced by zero. Cohort membership remains fixed,
and each cohort is rebalanced equally among members with usable returns in that
month. The report counts every unavailable constituent slot and records the
tie-breaking, delisting and standard-error assumptions.

This first 6/6 implementation is retained as a transparent development step.
The full Table I results below supersede its preliminary estimates because all
32 strategies use one common evaluation period and an unfiltered return lookup
after formation-date eligibility is fixed.

## Stage 4: complete Table I

Run the complete replication with:

```sh
python src/04_table1_replication.py
```

DuckDB scans the 26 GB daily CSV without loading it into memory. The script
validates and atomically rebuilds two compact processed return layers, then
constructs all J/K combinations for `J,K = 3,6,9,12`:

- `processed/table1_monthly_return_lookup.parquet`
- `processed/table1_daily_cumulative.parquet`
- `outputs/tables/table1_replication.csv`
- `outputs/tables/table1_monthly_returns.csv`
- `outputs/logs/table1_replication_report.txt`

Panel A uses contiguous formation and holding periods. Panel B ends the daily
formation-return window seven calendar days before the first trading day of the
holding month. The paper says to skip one week but does not state whether that
means five trading days or seven calendar days, so this is an explicit,
reviewable implementation assumption. Every strategy uses the same modern
evaluation period, January 2002 through December 2025.

## Audit definitions and limits

CSV has no intrinsic data types. The audit reports full-file counts of
integer-like, float-like and text tokens, preserving leading-zero identifiers
in duplicate checks. Missing means blank/whitespace-only. Nonblank special
missing codes are not silently mapped; nonnumeric/nonfinite MthRet/MthCap values
are flagged separately. Confirm source missing-code semantics before cleaning.

For monthly coverage only, a valid return is finite numeric and at least -1,
with a nonblank stock identifier and a parseable date. This is an audit definition,
not final research eligibility. Descriptive statistics include all finite values,
even flagged returns below -1 or nonpositive capitalization. No rows are dropped,
returns imputed, or outliers altered. Duplicate totals exclude missing keys;
missing identifiers/dates are separately flagged. Issue counts cover all rows
and may overlap. No observation-level example or duplicate files are exported.

Formation histories and holding-period eligibility remain portfolio-stage
checks. Six observations with incomplete delisting-return flags are retained
and require an explicit portfolio-stage decision.

## Findings and validation from the initial inspection

The supplied file covers January 2000–December 2025, so it cannot reproduce
results for the original 1965–1989 evaluation period. A modern-period 6/6
investigation requires an explicitly documented sample-period deviation or
additional historical data. The actual file contains exchange/security fields,
contrary to the initial four-field description, and includes ETFs and closed-end
funds. Stage 2 restricts it to the approved CRSP CIZ equivalent of legacy share
codes 10 and 11 on NYSE or NYSE American.

All 22,447 duplicate groups were checked. Monthly return/capitalization conflicts
were zero; 20,905 groups were distribution fan-out and 1,542 historical
classification conflicts were uniquely resolved using effective dates. The
processed output has no duplicate PERMNO-month keys.

AI assisted with the inspection code and documentation. Initial verification
included syntax compilation and a diagnostic fixture covering duplicates, dates,
missing/nonnumeric fields, the -100% boundary, capitalization, monthly distinct
stocks and exact percentiles. Full-file monthly and duplicate totals reconciled.
The Stage 2 run also confirmed the raw file was unchanged using SHA-256.
The original-period feasibility expectation and the assumption of no monthly
exchange fields were corrected using actual data. Formal AI-use reporting will
be developed with the research report.
