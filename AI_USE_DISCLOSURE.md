# AI-Use Disclosure

**Project:** ECON6067 — Momentum Replication of Jegadeesh and Titman (1993)

## 1. Where and how I used AI

I used ChatGPT and AI coding assistance in VS Code during this project. As I was still learning how to implement an empirical finance study in Python, AI helped me turn the procedures in Jegadeesh and Titman (1993) into working scripts. This included inspecting and filtering the CRSP data, constructing momentum portfolios, calculating the results for Tables I–III, and building the independent extension comparing my 6/6 winner-minus-loser (WML) strategy with Kenneth French's Momentum Factor.

I also used AI to debug errors, design validation checks, organize the reproducibility workflow, write the README and Skill instructions, and improve the English in my empirical report. 

## 2. How I checked the outputs

I did not assume that AI-generated code or explanations were correct simply because they ran without errors. I checked the results against the original paper's portfolio-construction rules and reviewed the validation reports produced by the project.

For Table I, a separate validation script reconstructed momentum rankings, portfolio membership, overlapping holdings and monthly returns, and checked that WML equalled the winner return minus the loser return. For Tables II and III, the checks included whether the benchmark 6/6 portfolio returns agreed across tables, as well as tests of beta estimation and regressions. In the extension, I checked that French's Mom factor was converted from percentage units to decimal returns and that observations were matched by month.

I also compared the economic interpretation in my report with the actual output tables. In particular, I distinguished positive average returns from statistically significant returns. The workflow's `--check` command was used to check inputs and execution settings, but this check alone is not a complete rerun of the analysis.

## 3. AI suggestions I corrected or did not adopt

One issue was how closely to follow the instructor's example repository. AI initially suggested reorganizing my existing project into a different folder structure. I decided to retain the working directory structure and instead document the actual paths, because moving files just for appearance could break an already functioning workflow.

Another issue concerned the portfolio construction assumptions. The paper's one-week gap required a practical choice, so the code uses seven calendar days and I documented this rather than claiming that the choice exactly reproduces every detail of the original study. Similarly, the treatment of missing holding-period returns and delisted stocks needed care. I did not treat missing returns as zero; the implementation uses available delisting-adjusted returns and reweights holdings when subsequent returns are unavailable. I reported this as a limitation rather than assuming that a mechanically consistent result was necessarily economically exact.

I also had to be careful not to overstate the findings. In my modern sample, the benchmark 6/6 WML return is positive but not statistically significant at the two-sided 5% level, even though its CAPM-adjusted alpha is significant. The French Momentum Factor is also constructed differently from the original JT portfolios, so I treated it as a comparison rather than an identical benchmark.

AI was helpful in completing the project, but it also required me to make and document methodological choices. I take responsibility for the final submitted code, results and interpretation, including any remaining errors.
