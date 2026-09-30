# SimplyShop: Trustworthy AI Product Discovery

University of Houston submission for the 2026 HSI Battle of the Brains.

Full site (shopper side and company side): https://simplyshop-uh.lovable.app

Company dashboard on its own: https://kjscoding.github.io/UH_TECH_09302026/

Start here if you are evaluating the technical implementation: the `monitor/` folder. It is the backend: it asks the AI assistants, checks every answer for hallucinations, traces the source, scores the harm, routes fixes through governance, and writes it all to a database. The dashboard sits on top.

What is real and what is simulated: demo mode uses reproducible sample AI responses so judges do not need API keys. The claim checker, hallucination detection, source tracing, severity scoring, governance routing, database, and every metric run for real on that sample, in both the backend and the dashboard. Add one API key and `--live` to collect real answers. Dell prices, competitor names, and the shopper insights, returns, tickets and trust figures are sample data.

## Architecture

```mermaid
flowchart LR
  Q[Shopper questions] --> A[AI assistant APIs<br/>ChatGPT, Gemini, Claude, Perplexity]
  A --> C[Claim extraction]
  T[Product record<br/>brand feed, retailer APIs] --> H
  C --> H[Hallucination checker]
  H --> S[Severity + source trace]
  S --> D[(SQLite / Postgres<br/>sweeps, answers, claims, actions, source_pages)]
  D --> B[Company dashboard]
  B --> G{Automatic or<br/>needs a person?}
  G -->|auto| F[Fix at the source]
  G -->|approve| P[Named owner signs off] --> F
  F --> R[Re-run questions] --> A
  D --> X[Shopper extension<br/>verified price on the AI page]
```

The loop is the product: AI answer, wrong claim, truth comparison, likely source, harm score, controlled fix, human approval when needed, re-run, measured improvement.

## What it does

Shoppers now ask AI assistants "what's the best laptop under $500?" and buy from the short answer they get back. If the assistant leaves a brand out, or gets its price, specs, stock or return policy wrong, the brand loses the sale or eats the return. SimplyShop gives a company a control room for that new front door.

1. Visibility Monitor: asks a library of real shopper questions across ChatGPT, Gemini, Perplexity, Claude and Copilot (live with an API key, sample answers in demo mode), then scores whether the brand appears, at what position, and next to which competitors.
2. Hallucination Watch: splits every AI answer into individual claims and checks each one against the company's product record. Catches incorrect pricing, availability, features and policies, plus fabricated products (retired or never existed) and misleading comparisons against them. Each one gets a severity score and is traced to the page the AI most likely learned it from.
3. Source Tracing: matches each wrong fact to the cited page it most likely came from (a stale retailer listing, an old review, an archived spec sheet) so the team fixes the cause, not the symptom.
4. Fix Engine and Approvals: scores each error for customer harm and routes it. Safe fixes that only copy an approved fact (like resyncing the brand's own product feed) run automatically. Anything that publishes new copy, contacts an outside company, or touches policy wording waits for a named owner to approve.
5. Change Impact: logs every change the company makes (page updates, price changes, retailer corrections) and shows whether inclusion and accuracy went up or down after it.
6. Shopper Insights: from opt in SimplyShop extension users, totals only. Why shoppers picked a competitor, head to head win rates, top questions and budgets.
7. Business Impact: returns and support tickets matched to each live hallucination (product, reason and date), the estimated weekly cost, money saved as they get fixed, shopper trust, and partner deal conversion.
8. Alerts: high severity errors go straight to the owner; the rest roll into a weekly digest.
9. Audit and Governance: every action is logged with who took it and why. Owners, action tiers and scale controls are laid out in the Governance view.

## How to use the demo

The demo loads a real sponsor brand, Dell, with sample product data and placeholder competitors. Assistant answers are simulated sample runs so judges can use it without API keys. The claim checker, source tracing, severity scoring, routing rules and metrics all run live in the browser.

Suggested path (about 3 minutes):

1. Overview: see inclusion, fact accuracy, wrong facts live and average position.
2. Visibility: click any cell to read that assistant's answer with each claim highlighted green (correct) or red (wrong).
3. Hallucination Watch: every hallucination by kind, with severity, likely origin, and the routed action.
4. Sources: which pages are causing the most wrong facts.
5. Approvals: approve or reject fixes and content gaps as the accountable owner.
6. Click "Re-run all questions" at the top. Approved fixes take effect and the metrics and trend chart update.
7. Try it: paste any AI answer about a Dell product and the checker grades it claim by claim.


## The monitor: how the data actually gets collected

The `monitor/` folder is a small, working version of the backend. It is what runs on a schedule in production. No framework, no install, Python 3 only.

```
cd monitor
python3 test_checker.py     # 12 tests: claims, hallucinations, tracing, price parsing, database round trip
python3 run_sweep.py        # demo mode, no API keys needed
python3 query_db.py         # read the results back from the database
python3 run_sweep.py --live # asks the real assistants (set OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY or PERPLEXITY_API_KEY)
```

Expected test output:

```
ok  test_correct_price_and_specs
ok  test_wrong_price_is_flagged
ok  test_budget_is_not_a_price_claim
ok  test_competitor_numbers_are_not_credited_to_us
ok  test_missing_touchscreen_is_caught
ok  test_policy_without_product_name
ok  test_rank_and_severity
ok  test_structured_price_parser
ok  test_fabricated_and_retired_products
ok  test_misleading_comparison_against_retired_product
ok  test_real_products_are_not_flagged_as_fabricated
ok  test_database_round_trip
12 tests passed
```

Expected sweep output (demo mode):

```
SimplyShop sweep for Dell  (40 answers, 62 claims checked)
  Inclusion in shopping answers: 48%
  Fact accuracy:                 79%
  Hallucinations live:           13  (5 high severity)
  ...
Saved sweep #1 to simplyshop.db (tables: sweeps, answers, claims, actions, source_pages)
```

What one sweep does:

1. Asks every AI assistant each question in `data/questions.json` through its official API (`simplyshop/assistants.py`). No scraping of chat sites. Perplexity returns the pages it used, which powers Source Tracing.
2. Splits each answer into claims and checks them against the brand's product record in `data/products.json` (`simplyshop/checker.py`). Wrong prices, specs, stock, features and policies are flagged, scored for customer harm, and traced to the cited page whose snapshot contains the wrong value.
3. Saves everything to a SQLite database, `monitor/simplyshop.db` (tables: sweeps, answers, claims, actions, source_pages), plus a JSON copy in `output/`. `python3 query_db.py` shows the latest sweep from SQL, `--export` writes the JSON the dashboard reads, and `--sql "..."` runs any query. Any SQLite viewer opens the file. Swapping to Postgres is one connection line.

Demo mode uses `data/sample_answers.json`, so judges can run it with zero keys and get the same numbers the dashboard shows (48% inclusion, 79% accuracy, 13 hallucinations, 5 high severity).

Where the real prices come from (`simplyshop/prices.py`), most reliable first: the brand's own product feed, then retailer and affiliate APIs, and only then reading a public product page. For pages we read the structured product data (schema.org JSON) rather than scraping HTML, check robots.txt first, keep a slow rate, and never go behind a login.

Production layout: these scripts run on a scheduler (cron or GitHub Actions) a few times a day, write to a Postgres database, and the dashboard reads from it. An LLM extracts claims from messier answers and this rule based checker validates them.

## How this answers the case prompt

| The prompt asks | Where it is answered |
|---|---|
| Track whether products or the brand show up in AI answers | Dashboard: Overview and Visibility tabs. Backend: `monitor/run_sweep.py`, `answers` table (brand_rank column) |
| What information the AI seems to rely on | Dashboard: Sources tab. Backend: source tracing in `checker.py`, `source_pages` table |
| Whether updates to the site, product pages, pricing or reviews help or hurt | Dashboard: Change Impact tab (before and after every change, live rows added when fixes are approved and questions re-run) |
| Detect incorrect or misleading information (features, pricing, availability, policies, comparisons) | Dashboard: Hallucination Watch tab, grouped by kind in the prompt's own words. Backend: `checker.py` (`HALLUCINATION_KIND`), `claims` table |
| Hallucinations about products that do not exist | Fabricated product and misleading comparison detection (`_phantoms` in `checker.py`), retired products in `data/products.json` |
| Measure the impact of those failures (returns, support costs, trust) | Dashboard: Business Impact tab, returns and tickets matched per hallucination, cost model, shopper trust rating |
| Use AI where it adds clear value | Automated daily question runs across five assistants, claim checking, severity scoring, fix drafting; an LLM extracts claims in production and this checker validates them |
| Which actions are automatic vs. need a person, and who is accountable | Dashboard: Approvals and Governance tabs. Backend: `route()` in `db.py`, `actions` table (mode, owner, status, decided_by) |
| Keep oversight working at scale | Governance tab: severity routing, monthly checker accuracy test, 5% spot checks, one adapter per assistant, per team views |
| Ethics: no unfair recommendations, privacy | Partners can never buy rank; every partner deal labeled; sharing off by default; Global Privacy Control honored; what the brand can and cannot see (Governance tab, shopper site Privacy Center) |
| Show improved inclusion, conversion and trust | Overview scorecard and trend chart; approve fixes, re-run, watch the numbers move; extension conversion and trust figures |

How this differs from AI visibility monitoring tools (Profound and similar): those stop at telling marketing what the AI said. SimplyShop closes the loop: it traces the wrong claim to its source, scores the harm in returns and tickets, routes the fix through named owners, re-runs the questions and proves the fix worked, and corrects the answer for the shopper at the moment of purchase through the extension.

What is real in this repo and what is simulated: the checker, source tracing, severity scoring, routing rules, database and metrics all run for real, on both the backend and in the dashboard. The AI answers, Dell prices, competitor names, and the shopper insights, returns, tickets and trust figures are sample data so judges can use everything without API keys or Dell's private data. Run `python3 run_sweep.py --live` with an API key to collect real answers.

## Tech

1. Plain HTML, CSS and JavaScript in one page (`src/app.html`), no framework and no build tools, so it hosts anywhere for free.
2. Claim extraction: rule based parser tuned to product facts (prices, specs, stock words, policy terms), with competitor clauses stripped so their numbers are never credited to the brand.
3. Source tracing: matches the wrong value against a snapshot of each cited page.
4. Severity: fact type weight plus shopping intent plus assistant reach.
5. Hosted on GitHub Pages.

Production path: the same checker sits behind a scheduled job that calls each assistant's API, stores answers and claims in a database, and uses an LLM to extract claims from free form answers, with this rule based checker kept as a validator. See the Governance view for the controls that keep it trustworthy as it scales.

## How to run it

Easiest: open the live demo link above. Nothing to install.

Locally (needs bash and python3):

```
./run.sh
```

Then open http://localhost:8080. `run.sh` builds `index.html` from `src/app.html`, starts a local server and checks that the app responds.

## Files

1. `src/app.html`: the dashboard (markup, styles, data and engine)
2. `monitor/`: the backend (ask the AIs, check for hallucinations, trace the sources, write to SQLite) with tests and demo data
2. `build.sh`: wraps the app into `index.html`
3. `index.html`: the built page GitHub Pages serves
4. `run.sh`: build and serve locally

## Team

University of Houston, HSI Battle of the Brains 2026.
