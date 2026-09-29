# SimplyShop: Trustworthy AI Product Discovery

University of Houston submission for the 2026 HSI Battle of the Brains.

Full site (shopper side and company side): https://simplyshop-uh.lovable.app

Company dashboard on its own: https://kjscoding.github.io/UH_TECH_09302026/

## What it does

Shoppers now ask AI assistants "what's the best laptop under $500?" and buy from the short answer they get back. If the assistant leaves a brand out, or gets its price, specs, stock or return policy wrong, the brand loses the sale or eats the return. SimplyShop gives a company a control room for that new front door.

1. Visibility Monitor: asks a library of real shopper questions across ChatGPT, Gemini, Perplexity, Claude and Copilot, then scores whether the brand appears, at what position, and next to which competitors.
2. Truth Check: splits every AI answer into individual claims (price, RAM, storage, battery, touchscreen, stock, returns, warranty) and checks each one against the company's product record, the single source of truth.
3. Source Tracing: matches each wrong fact to the cited page it most likely came from (a stale retailer listing, an old review, an archived spec sheet) so the team fixes the cause, not the symptom.
4. Fix Engine and Approvals: scores each error for customer harm and routes it. Safe fixes that only copy an approved fact (like resyncing the brand's own product feed) run automatically. Anything that publishes new copy, contacts an outside company, or touches policy wording waits for a named owner to approve.
5. Change Impact: logs every change the company makes (page updates, price changes, retailer corrections) and shows whether inclusion and accuracy went up or down after it.
6. Shopper Insights: from opt in SimplyShop extension users, totals only. Why shoppers picked a competitor, head to head win rates, top questions and budgets.
7. Business Impact: estimated weekly cost of wrong facts still live, money saved as they get fixed, shopper trust, and partner deal conversion.
8. Alerts: high severity errors go straight to the owner; the rest roll into a weekly digest.
9. Audit and Governance: every action is logged with who took it and why. Owners, action tiers and scale controls are laid out in the Governance view.

## How to use the demo

The demo loads a real sponsor brand, Dell, with sample product data and placeholder competitors. Assistant answers are simulated sample runs so judges can use it without API keys. The claim checker, source tracing, severity scoring, routing rules and metrics all run live in the browser.

Suggested path (about 3 minutes):

1. Overview: see inclusion, fact accuracy, wrong facts live and average position.
2. Visibility: click any cell to read that assistant's answer with each claim highlighted green (correct) or red (wrong).
3. Truth Check: every wrong fact with severity, likely origin, and the routed action.
4. Sources: which pages are causing the most wrong facts.
5. Approvals: approve or reject fixes and content gaps as the accountable owner.
6. Click "Re-run all questions" at the top. Approved fixes take effect and the metrics and trend chart update.
7. Try it: paste any AI answer about a Dell product and the checker grades it claim by claim.

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

1. `src/app.html`: the app (markup, styles, data and engine)
2. `build.sh`: wraps the app into `index.html`
3. `index.html`: the built page GitHub Pages serves
4. `run.sh`: build and serve locally

## Team

University of Houston, HSI Battle of the Brains 2026.
