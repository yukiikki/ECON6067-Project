# ECO6067 Project Data Downloads

Download only the numbered ZIP files required for your chosen project.

| Project | Required packages |
| --- | --- |
| Card and Krueger: minimum wage / DID | 01 |
| Fama and French: three-factor construction | 02 and 04 |
| Shleifer / Greenwood and Sammon: index effect | 03 and 04 |
| Jegadeesh and Titman: momentum | 02 and 03 |

## What is in each package?

- **01_DID.zip:** restaurant survey data (`public.csv`).
- **02_Monthly_Stocks_and_Factors.zip:** monthly stock data and Fama-French factors / risk-free rate.
- **03_Daily_Stocks.zip:** daily stock data, shared by the index-effect and momentum projects.
- **04_Supplementary_Data.zip:** Compustat annual data, the CRSP-Compustat link table, and index-change events. The Fama-French project uses `Compustat.csv` and `CCM.csv`; the index-effect project uses `index_date.csv`.

## How to use the downloads

1. Download the required ZIP files individually.
2. Extract them into the same project working directory. Each ZIP creates a distinct numbered folder.
3. Keep the folder names and CSV filenames unchanged. Point your analysis code to the relevant `data/` folders.
4. Read `DATA_DICTIONARY.md` for column definitions. The dictionary's original project labels refer to the source datasets; the numbered packages above are their download locations.

Package 03 expands to approximately **28.39 GB**. Allow at least **40 GB of free local disk space** when using it, so that the downloaded ZIP and extracted CSV can coexist. Load large CSV files with an appropriate tool or in chunks rather than opening them in a spreadsheet application.

The CSV contents are unchanged from the supplied data archive. Shared datasets appear only once across the four packages. Package 02 expands to approximately 1.24 GB; package 04 to approximately 39 MB.

Use these files for the course project within the applicable data-access terms. Do not include restricted raw data in a public GitHub repository; document its location and access requirements instead.
