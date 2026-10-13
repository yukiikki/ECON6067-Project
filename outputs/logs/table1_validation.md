# Independent validation of Table I

**Overall status: CONDITIONALLY VALIDATED**

This script is independent of `src/04_table1_replication.py`: it does not import
or call any original portfolio-construction function. Existing raw, processed,
and result files were opened read-only. The only generated artifact is this
report.

Deterministic full-universe formation dates: 200112, 201312, 202511.  
Deterministic J=6/K=6 holding months: 200201, 201401, 202512.

## Test results

| Test | Method | Status | Numerical evidence | Issue / assumption |
|---|---|---:|---|---|
| Raw-monthly J=6 formation replication | Read the original monthly CSV for all eligible stocks on three deterministic formation dates; independently compound six returns and rank the full universe. | PASS | 200112: eligible=2045, ranked=2013, P1/P10=202/201, decile mismatches=0; 201312: eligible=1526, ranked=1483, P1/P10=149/148, decile mismatches=0; 202511: eligible=1384, ranked=1360, P1/P10=136/136, decile mismatches=0; raw duplicate excess rows reconciled=61 | None |
| Raw-daily Panel B replication | Read the original 26 GB daily CSV for the full eligible universes on the selected dates; compound daily returns over the independently derived six-month windows. | PASS | 200112: 2001-06-26..2001-12-26, formation=2002-01-02, gap=7 days, ranked=2015, decile mismatches=0, P1/P10 membership differences versus Panel A=136; 201312: 2013-06-26..2013-12-26, formation=2014-01-02, gap=7 days, ranked=1475, decile mismatches=0, P1/P10 membership differences versus Panel A=62; 202511: 2025-05-23..2025-11-24, formation=2025-12-01, gap=7 days, ranked=1358, decile mismatches=0, P1/P10 membership differences versus Panel A=53 | Panel B uses seven calendar days because the paper does not define calendar versus trading week. |
| Selected-date portfolio arithmetic | For early, middle, and late J=6/K=6 months, independently obtain raw monthly holding returns for all members of all six cohorts; equal weight within cohort and across cohorts. | PASS | A 200201: Sell=0.01703299, Buy=0.03346730, WML=0.01643431, max diff=0.00e+00; B 200201: Sell=0.02243066, Buy=0.03159172, WML=0.00916106, max diff=0.00e+00; A 201401: Sell=0.02230629, Buy=-0.01559267, WML=-0.03789896, max diff=0.00e+00; B 201401: Sell=0.02440113, Buy=-0.01978071, WML=-0.04418184, max diff=0.00e+00; A 202512: Sell=-0.02121723, Buy=0.00929708, WML=0.03051431, max diff=0.00e+00; B 202512: Sell=-0.02270746, Buy=0.01034600, WML=0.03305346, max diff=0.00e+00 | Unavailable returns are omitted only to reproduce the documented existing decision. |
| Full monthly return reconstruction | Independently reconstruct all 9,216 strategy-month observations from rebuilt cohorts and compare Sell, Buy, and WML with the published monthly CSV. | PASS | rows=9,216; maximum absolute difference=0.000e+00; active cohort range=3-12; invalid cohort ages=0 | None |
| Output invariants and weights | Check every published strategy-month key, WML identity, active-cohort count, independently rebuilt decile uniqueness/order/size, and equal-weight sums. | PASS | duplicate output keys=0; WML max error=5.551e-17; invalid active-cohort rows=0; decile sizes=131-227; weight-sum range=0.9999999999999999-1.0000000000000000 | Exact signal ties use ascending PERMNO, matching the documented deterministic assumption. |
| Summary statistics | Use Python statistics.fmean/stdev on the independently rebuilt monthly series; compute SE=s/√T and t=mean/SE for all 96 cells. | PASS | summary rows=96; max mean diff=4.981e-11; max SE diff=4.952e-11; max t diff=4.890e-05 | Conventional t-statistics do not correct serial dependence from overlapping portfolios. |
| Common evaluation period | Check the published calendar and test whether all 32 strategies possess every required cohort in December 2001 and January 2002. | PASS | months=288; range=200201-202512; all strategies valid in 200112=False; in 200201=True | January 2002 is the first month with the extra Panel B week plus 12-month formation and 12 overlapping holding cohorts available from data beginning in January 2000. |
| Missing returns and delisting treatment | Classify every unusable component slot by raw-month history position and final delisting flag; confirm that the existing calculation omits it and renormalizes the remaining cohort members. | WARNING | unusable=1,010,415/22,035,515 (4.59%); unique stock-month-reasons=23,612; after_complete_delisting=873,188, after_history_end_unresolved=8,463, after_incomplete_delisting=68,108, incomplete_delisting_G=198, incomplete_delisting_M=15,179, missing_return=45,279 | Renormalizing available securities is documented and never inserts zero, but missingness/delisting is nonrandom and can change returns. The paper and supplied documentation do not fully specify an alternative for incomplete G/M returns. |
| Panel B one-week convention | Compare first holding trading dates with raw-daily signal endpoints on selected dates and confirm no daily return after the endpoint enters the signal. | WARNING | full-sample realized calendar gaps=7-10 days (7d=288, 8d=6, 10d=11); selected gaps=[7, 7, 7]; boundary mismatches=0; raw daily decile mismatches=0 | The seven-calendar-day rule is internally correct and uses daily data, but the paper does not state whether 'one week' means seven calendar days or five trading days. |
| Look-ahead and date alignment | For every reconstructed output, require formation months h-K..h-1; Panel A signals end in formation month t and Panel B daily signals end before the first trading day of t+1. | PASS | invalid cohort ages=0; Panel B boundary mismatches=0; selected gaps=[7, 7, 7] | Eligibility is observed in month t; no holding-month return or future availability determines formation eligibility. |
| Read-only input/result integrity | Compare byte size and nanosecond modification time for every raw, processed, and existing Table I result file before and after validation. | PASS | files checked=7; changed files=0 | None |

## Assessment

The numerical implementation is independently reproduced and all required invariants pass. Table I is computationally validated, but should be described as methodologically conditional until the seven-calendar-day Panel B convention and the renormalization treatment for unavailable holding returns are approved or accompanied by robustness checks.

No research result or existing Table I file was changed by this validation.
