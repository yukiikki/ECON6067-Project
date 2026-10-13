# Project Data Dictionary

Columns used by each project and their meanings.

## CSV Files

| Project | CSV files |
|---|---|
| DID | `public.csv` |
| Fama3 | `monthly_stock.csv`, `Compustat.csv`, `CCM.csv`, `F-F factors and RF.csv` |
| Index Effect | `daily_stock.csv`, `index_date.csv` |
| Momentum | `daily_stock.csv`, `monthly_stock.csv`, `F-F factors and RF.csv` |

## 1. DID

### `public.csv`

Store-level survey data: 410 observations and 46 columns. The fields used in the analysis are listed below. Suffix `2` indicates the second survey; blank values indicate missing data.

| Column | Meaning |
|---|---|
| `SHEET` | Original store/survey sheet identifier. |
| `CHAIN` | Chain: 1 = Burger King; 2 = KFC; 3 = Roy Rogers; 4 = Wendy’s. |
| `CO_OWNED` | Company ownership indicator: 1 = company-owned. |
| `STATE` | State: 1 = New Jersey; 0 = Pennsylvania. |
| `SOUTHJ` | 1 if in southern NJ |
| `CENTRALJ` | 1 if in central NJ |
| `PA1` | 1 if located in the northeastern Philadelphia suburbs in Pennsylvania. |
| `PA2` | 1 if located in the Easton area of Pennsylvania. |
| `EMPFT` | Number of full-time employees |
| `EMPPT` | Number of part-time employees |
| `NMGRS` | Number of managers and assistant managers |
| `WAGE_ST` | Starting wage, in dollars per hour. |
| `BONUS` | 1 if a cash recruiting bonus is offered for new workers. |
| `HRSOPEN` | Hours open per day. |
| `PSODA` | price of medium soda, including tax |
| `PFRY` | price of small fries, including tax |
| `PENTREE` | price of entree, including tax |
| `STATUS2` | Second-wave status: 0 = refused; 1 = answered; 2 = renovation closure; 3 = permanent closure; 4 = highway-construction closure; 5 = mall-fire closure. |
| `EMPFT2` | Number of full-time employees |
| `EMPPT2` | Number of part-time employees |
| `NMGRS2` | Number of managers and assistant managers |
| `WAGE_ST2` | Starting wage, in dollars per hour. |
| `SPECIAL2` | 1 if a special program is offered for new workers. |
| `HRSOPEN2` | Hours open per day. |
| `PSODA2` | price of medium soda, including tax |
| `PFRY2` | price of small fries, including tax |
| `PENTREE2` | price of entree, including tax |

## 2. Fama3

### `monthly_stock.csv`

| Column | Meaning |
|---|---|
| `PERMNO` | CRSP permanent security identifier. |
| `PERMCO` | CRSP permanent company identifier. |
| `MthCalDt` | Monthly observation date. |
| `MthRet` | Monthly total return, as a decimal. |
| `MthRetx` | Monthly return excluding distributions, as a decimal. |
| `MthPrc` | Monthly security price. |
| `ShrOut` | Shares outstanding, in thousands of shares. |
| `MthCap` | Reported monthly market capitalization. |
| `MthPrevCap` | Market capitalization at the previous observation. |
| `MthPrevDt` | Date of the previous observation. |
| `MthDelFlg` | Monthly delisting flag. |
| `SecInfoStartDt` | Start date for the historical security-information record. |
| `SecInfoEndDt` | End date for the historical security-information record. |
| `PrimaryExch` | Primary exchange code. |
| `ShareType` | Share-type classification. |
| `SecurityType` | Broad security classification. |
| `SecuritySubType` | Security subtype. |
| `USIncFlg` | U.S. incorporation flag. |
| `IssuerType` | Issuer classification. |
| `ConditionalType` | Trading-condition classification. |
| `TradingStatusFlg` | Trading-status flag. |

### `Compustat.csv`

Company financial statements. Accounting amounts are in millions of dollars for the USD sample.

| Column | Meaning |
|---|---|
| `gvkey` | Compustat company identifier. |
| `datadate` | Accounting statement date. |
| `indfmt` | Industry-format code. |
| `datafmt` | Data-format code. |
| `consol` | Consolidation-level code. |
| `curcd` | Reporting currency. |
| `pstkrv` | Preferred stock, redemption value. |
| `pstkl` | Preferred stock, liquidation value. |
| `pstk` | Preferred stock, carrying value. |
| `seq` | Total stockholders’ equity. |
| `ceq` | Common equity. |
| `at` | Total assets. |
| `lt` | Total liabilities. |
| `txditc` | Deferred taxes and investment tax credit. |

### `CCM.csv`

| Column | Meaning |
|---|---|
| `gvkey` | Compustat company identifier. |
| `LPERMNO` | Linked CRSP permanent security identifier. |
| `LINKTYPE` | Link-quality/type classification. |
| `LINKPRIM` | Primary-link classification. |
| `LINKDT` | Link start date. |
| `LINKENDDT` | Link end date. |

### `F-F factors and RF.csv`

Monthly factors and risk-free returns, reported in percent. The first field is the month in `YYYYMM` format.

| Column | Meaning |
|---|---|
| `Unnamed first field (YYYYMM)` | Monthly date key. |
| `Mkt-RF` | Official market excess return. |
| `SMB` | Official small-minus-big size factor. |
| `HML` | Official high-minus-low book-to-market factor. |
| `RF` | Monthly risk-free return. |

## 3. Index Effect

### `index_date.csv`

Index additions and deletions: 1,902 events, with effective dates from September 1976 to December 2021. Dates use `YYYY-MM-DD`.

| Column | Meaning |
|---|---|
| `permno` | CRSP permanent security identifier. |
| `ann` | Index-change announcement date. |
| `eff` | Index-change effective date. |
| `add` | Index-change direction: 1 = addition; 0 = deletion. |

### `daily_stock.csv`

| Column | Meaning |
|---|---|
| `PERMNO` | CRSP permanent security identifier. |
| `DlyCalDt` | Daily observation date. |
| `DlyRet` | Daily security total return, as a decimal. |
| `sprtrn` | S&P 500 daily index return, as supplied in the source. |
| `DlyPrevDt` | Previous observation date associated with the return. |
| `DlyRetDurFlg` | Daily return-duration flag. |
| `DlyRetMissFlg` | Daily return-missing flag. |
| `DlyDelFlg` | Daily delisting flag. |

Event returns are calculated from daily stock and market returns; `index_date.csv` contains event identifiers and dates only.

## 4. Momentum

### `daily_stock.csv`

| Column | Meaning |
|---|---|
| `PERMNO` | CRSP permanent security identifier. |
| `DlyCalDt` | Daily observation date. |
| `DlyRet` | Daily security total return, as a decimal. |
| `DlyPrevDt` | Previous observation date associated with the return. |
| `DlyRetDurFlg` | Daily return-duration flag. |
| `DlyDelFlg` | Daily delisting flag. |
| `DlyCap` | Daily market capitalization. |
| `PrimaryExch` | Primary exchange code. |
| `SecurityType` | Broad security classification. |
| `SecuritySubType` | Security subtype. |
| `ShareType` | Share-type classification. |
| `TradingStatusFlg` | Trading-status flag. |
| `ConditionalType` | Trading-condition classification. |
| `SecInfoStartDt` | Start date for the historical security-information record. |
| `SecInfoEndDt` | End date for the historical security-information record. |
| `vwretd` | CRSP value-weighted market return including distributions, as a decimal. |
| `SecurityEndDt` | End date of the security’s history. |

### `monthly_stock.csv`

| Column | Meaning |
|---|---|
| `PERMNO` | CRSP permanent security identifier. |
| `MthCalDt` | Monthly observation date. |
| `MthRet` | Monthly security total return, as a decimal. |
| `MthCap` | Reported monthly market capitalization. |

### `F-F factors and RF.csv`

Only the monthly date and risk-free return are used. Returns are reported in percent.

| Column | Meaning |
|---|---|
| `Unnamed first field (YYYYMM)` | Monthly date key. |
| `RF` | Monthly risk-free return, in percent in the source. |

## Units

CRSP return fields are decimals (0.01 = 1%). Fama–French factor and RF values are percentages (1.00 = 1%). The factor CSV files include introductory text and annual data; the projects use the monthly section.
