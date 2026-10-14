# ECON6067: Jegadeesh and Titman (1993) momentum replication

This repository reproduces Tables I-III of Jegadeesh and Titman (1993),
*Returns to Buying Winners and Selling Losers: Implications for Stock Market
Efficiency*, using the supplied modern CRSP sample. It also compares the
replicated six-month formation/six-month holding winner-minus-loser return with
the Kenneth French monthly Momentum Factor.

The empirical analysis is complete. The numerical results cover January 2002
through December 2025 and should not be expected to match the paper's
January 1965-December 1989 estimates.

## Research outputs

- **Table I:** all 32 combinations of formation and holding periods
  `J,K = 3,6,9,12`, for the no-skip and one-week-gap panels.
- **Table II:** post-ranking market betas and average market capitalization for
  the no-skip `J=6, K=6` deciles.
- **Table III:** size- and ex-ante-beta-sorted `J=6, K=6` portfolios, including
  raw returns, CAPM alphas, and joint tests.
- **Independent Extension:** descriptive statistics, correlation, OLS
  regression, and cumulative returns for replicated 6/6 WML versus French Mom.

Detailed implementation assumptions and validation evidence are recorded in
`outputs/logs/`. The code does not force the modern results to equal the
original paper.

## Data access and required files

The CRSP-derived course files are restricted and are not distributed through
this public repository. Obtain packages 02 and 03 through the authorized
ECON6067 course distribution, extract them at the project root, and preserve
the supplied folder names and filenames:

```text
02_Monthly_Stocks_and_Factors/
├── README.md
├── DATA_DICTIONARY.md
└── data/
    ├── monthly_stock.csv
    └── F-F factors and RF.csv

03_Daily_Stocks/
├── README.md
├── DATA_DICTIONARY.md
└── data/
    └── daily_stock.csv
```

The monthly package is approximately 1.2 GB and the daily CSV is approximately
28 GB after extraction. Allow at least 40 GB of free disk space for the course
archives, extracted files, processed caches, and DuckDB temporary work.

The Independent Extension reads
`external_data/F-F_Momentum_Factor 2.csv`, a local snapshot of the Kenneth
French monthly Momentum Factor. The source file contains explanatory text,
monthly observations, and annual observations. The script extracts only exact
six-digit `YYYYMM` rows, converts percent returns to decimals, and handles the
documented missing markers. The official source is the Kenneth French Data
Library, “Momentum Factor (Mom)” monthly CSV:

<https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_CSV.zip>

Do not alter any source CSV manually. The workflow verifies that research
inputs remain unchanged during execution.

## Project structure

```text
ECON6067_Project/
├── 02_Monthly_Stocks_and_Factors/   # supplied monthly package
├── 03_Daily_Stocks/                 # supplied daily package
├── external_data/                   # French Mom source snapshot
├── reference/                       # paper used for methodology
├── src/
│   ├── 01_data_inspection.py
│   ├── 02_data_preparation.py
│   ├── 03_momentum_j6_k6.py         # preliminary development result
│   ├── 04_table1_replication.py
│   ├── 05_table2_replication.py
│   ├── 06_table3_replication.py
│   ├── 07_extension.py
│   └── validation/
│       └── validate_table1.py
├── processed/                       # regenerated intermediates; Git-ignored
├── outputs/
│   ├── tables/                      # final and supporting CSV results
│   ├── figures/                     # final figures
│   └── logs/                        # audit and validation reports
├── run_all.py                       # main reproducibility workflow
├── requirements.txt
├── README.md
└── .gitignore
```

All scripts derive the project root from their own file location with
`pathlib`. Running them does not require an absolute path or editing source
code.

## Environment setup

Python 3.10 or newer is required. The project was last tested with Python 3.13
and the exact package versions in `requirements.txt`.

From the project root on macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Only `duckdb` and `pyarrow` are third-party runtime dependencies. Remaining
imports use the Python standard library. The cumulative-return figure is
written directly as SVG, so Matplotlib is not required.

## Reproduce the complete analysis

First verify that the environment, scripts, and four required input files are
available:

```sh
python run_all.py --check
```

This preflight does not run an analysis or rewrite an output.

Run the formal reproducibility workflow with:

```sh
python run_all.py
```

The default sequence is:

1. data inspection;
2. sample construction;
3. complete Table I;
4. Table II;
5. Table III;
6. Independent Extension.

The process stops immediately if a stage fails or an expected output is
missing. It uses the active Python interpreter and sets the working directory
to the project root for every stage.

The following checks are retained as optional work because they repeat parts of
the main pipeline:

```sh
# Include the preliminary standalone Panel A J=6/K=6 development result
python run_all.py --include-development-j6k6

# Include the independent Table I validation
python run_all.py --include-table1-validation

# Include both
python run_all.py --include-development-j6k6 --include-table1-validation
```

The full workflow scans the approximately 28 GB daily file when rebuilding
Table I. Runtime depends on disk speed and available memory. A check-only run is
the appropriate routine test when the empirical outputs have already been
validated.

## Main expected outputs

### Processed intermediates

These files are generated locally and excluded from Git because they are
reproducible and can be large:

- `processed/monthly_stock_prepared.parquet`
- `processed/table1_monthly_return_lookup.parquet`
- `processed/table1_daily_cumulative.parquet`
- `processed/table3_daily_market.parquet`
- `processed/table3_scholes_williams_betas.parquet`

### Tables and monthly series

- `outputs/tables/table1_replication.csv`
- `outputs/tables/table1_monthly_returns.csv`
- `outputs/tables/table2_replication.csv`
- `outputs/tables/table2_monthly_portfolios.csv`
- `outputs/tables/table3_replication.csv`
- `outputs/tables/table3_monthly_portfolios.csv`
- `outputs/tables/independent_extension_descriptive.csv`
- `outputs/tables/independent_extension_regression.csv`
- `outputs/tables/independent_extension_monthly.csv`

### Figure

- `outputs/figures/independent_extension_cumulative_returns.svg`

### Audit and validation reports

- `outputs/logs/inspection_report.txt`
- `outputs/logs/sample_construction_report.txt`
- `outputs/logs/table1_replication_report.txt`
- `outputs/logs/table1_validation.md`
- `outputs/logs/table2_replication_report.txt`
- `outputs/logs/table3_replication_report.txt`
- `outputs/logs/independent_extension_report.txt`

The two `table1_panel_a_j6_k6` CSVs and
`momentum_j6_k6_report.txt` document the preliminary development stage.
The complete Table I outputs supersede them for the reported replication.

## Core sample and implementation choices

The Stage 2 sample applies the approved CRSP CIZ common-stock criteria:

- `PrimaryExch` in `N` or `A`;
- `SecurityType=EQTY`;
- `SecuritySubType=COM`;
- `ShareType=NS`;
- `USIncFlg=Y`;
- `IssuerType` in `ACOR` or `CORP`.

The prepared panel contains 520,438 unique stock-month observations, 4,669
PERMNOs, and 312 months. Missing returns, valid extreme returns, delisting
observations, and missing or nonpositive capitalization are preserved with
explicit usability rules rather than globally dropped or imputed.

Table I uses equal-weight momentum deciles and overlapping holding cohorts.
Panel B implements the paper's one-week gap as seven calendar days, an explicit
assumption because the paper does not specify five trading days versus seven
calendar days. Table II and Table III assumptions, including beta equations,
capitalization timing, Scholes-Williams requirements, and inference conventions,
are stated in their individual reports.

The main tables use conventional time-series or OLS standard errors as
documented in the scripts and reports. Overlapping portfolios can produce
serial dependence, so conventional significance statistics should be
interpreted with that limitation.

## Git and data-protection rules

The repository tracks source code, documentation, concise logs, final tables,
the extension figure, and the small French Mom source snapshot. It excludes:

- the restricted course CSVs under both numbered packages;
- all generated `processed/` Parquet files;
- virtual environments and Python caches;
- DuckDB, SQLite, coverage, editor, and operating-system temporary files.

Before making a public repository, verify that the reference-paper PDF may be
redistributed. If redistribution is not permitted, keep the citation in the
README and remove only the PDF from Git tracking while retaining a local copy.

Do not use `git add -f` to override the raw-data exclusions. Review staged
files with `git status` and `git diff --cached --stat` before every push.

## Reproducibility limits

- The supplied data begin in 2000, so the original 1965-1989 sample cannot be
  reconstructed from these course files.
- Raw CRSP-derived files cannot be included in a public repository; another
  authorized user must obtain the same course packages.
- Generated reports may display machine-specific absolute paths for diagnostic
  clarity, but all input resolution and computation use repository-relative
  defaults.
- The French source snapshot reflects the version downloaded for this project;
  later versions of the French Data Library may revise historical data.
- No standalone output currently documents the alternative post-delisting cash
  treatment or five-trading-day Panel B gap discussed during robustness review.
  Do not cite those robustness results unless their code and outputs are restored.
- The final empirical report and AI-use disclosure should describe all approved
  deviations and how generated code and results were independently checked.
