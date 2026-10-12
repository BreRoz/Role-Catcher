# Role Catcher

Scans open roles on Greenhouse, Ashby, and Lever job boards, scores each against my background, checks the company is healthy, and emails me only roles that pass both tests. It does not write cover letters or apply for me.

## How it works

1. **Discover** open roles from many job boards.
2. **Pre-filter** by title, seniority, and location (plain code, no AI, free).
3. **Score** the role against `profile.md` using an AI model through OpenRouter. Default threshold: 75.
4. **Vet the company** (only for roles that passed step 3). Results are remembered for 60 days.
5. **Email** me only if both the role and the company pass.

Expensive steps run last, so nothing is scored or researched that a cheaper step already rejected.

## Files you will edit

| File | What it is |
|---|---|
| `config.yaml` | Thresholds, AI model names, daily caps, vetting rules |
| `filters.yaml` | Job titles to include/exclude, location rules |
| `profile.md` | My background, the scorer's source of truth about me |
| `rubric.md` | The scoring instructions and point weights sent to the model |
| `watchlist.yaml` | Priority companies |
| `calibration/`, `negative_examples/` | Example good and bad roles for tuning |

Files in `state/` are the agent's memory. It updates them itself after each run; don't edit by hand.

## One-time setup (no coding needed)

1. **Get an OpenRouter key.** Sign up at openrouter.ai, add a few dollars of credit, create an API key.
2. **Get a Gmail app password.** Turn on 2-step verification for your Google account, then go to myaccount.google.com/apppasswords and create one. It is a 16-character code.
3. **Add four secrets on GitHub.** In the repo: Settings → Secrets and variables → Actions → New repository secret. Add each of these:
   - `OPENROUTER_API_KEY`: your OpenRouter key
   - `SMTP_USER`: the Gmail address that sends the email
   - `SMTP_APP_PASSWORD`: the 16-character app password
   - `EMAIL_TO`: where alerts go (default is bre999@gmail.com)
4. **Allow the workflow to save its memory.** Settings → Actions → General → Workflow permissions → choose "Read and write permissions" → Save.
5. **Test it.** Actions tab → Role Catcher → Run workflow.

## OpenRouter: models, cost, and privacy

**Models.** `config.yaml` has two model slugs, `scoring_model` (role fit) and `vetting_model` (company research with web search). Each also has a fallback that OpenRouter uses automatically if the first model errors or its provider is down. Change any of them by editing the text; no code changes needed. Browse slugs at openrouter.ai/models.

**What it costs.** Every AI call's real cost (as reported by OpenRouter) is written to `logs/spend.jsonl`, one line per call. The daily caps use this log. Web search adds about $0.01 per search on top of the model's token cost.

**Set a monthly limit on your key (important).** In OpenRouter go to Keys, open the Role Catcher key, and set a monthly credit limit (for example $20). This is a second safety net in case something misbehaves.

**Keep your profile private.** `profile.md` is your career history, so:
1. In OpenRouter go to Settings, then Privacy. Leave **"OpenRouter Use of Inputs/Outputs"** turned OFF. (Turning it on gives a 1% discount but lets OpenRouter use your prompts.)
2. In Settings, Observability, leave **"Private Input & Output Logging"** turned OFF so prompts are not stored in your logs.
3. Every request the agent sends already includes `data_collection: deny`, so OpenRouter only routes to providers that do not store or train on prompts. If you want the strictest option, set `zdr: true` under `privacy` in `config.yaml` (zero-data-retention endpoints only); this may limit which models and features are available.

**How output is checked.** Scoring and vetting answers must match a strict format. The agent validates every answer in code and retries once with the error if it does not. A second failure stops that item instead of guessing.

**Company sources.** The sources shown for a company come only from the web search results OpenRouter returns, never from the AI's memory.

**Prompt caching.** Your profile is sent first in every scoring request and marked for caching, so repeated calls within a few minutes are billed at a small fraction of the normal input price.

## The pre-filter (free, no AI)

Before anything is scored, `filters.yaml` cuts roles by title, level, location, and pay. Edit that file to change the rules; no code changes needed.

- **Title:** kept only if it matches an include pattern and no exclude pattern. Matching ignores case and punctuation and needs whole words ("internal" does not trip "intern"). "Program manager" counts only alongside enablement, GTM, or systems. "Associate" is allowed for "associate director", "designer" for "solutions designer", and "specialist" when the job description is clearly about building systems.
- **Level:** only titles that are clearly junior are dropped (for example "analyst"). An unclear level goes through to scoring.
- **Location:** fully remote (US), Austin, or NYC. A remote role that names only other countries is dropped. Odd or empty locations are kept with a "check location" flag.
- **Pay:** compared against the midpoint of the listed range. Remote or Austin roles need $130k, NYC-only roles need $190k. If a posting lists several pay bands, one meeting the floor is enough. No pay listed is never a reason to drop; NYC roles with no pay get a "check comp" flag. NYC roles are always flagged "NYC".
- **Audit:** every dropped role and its reason is written to `logs/last_run.json` (section `prefilter`), along with the roles that were kept.

## Role fit scoring

Each role that survives the pre-filter is sent to the scoring model with `rubric.md`, `profile.md`, and (when present) short calibration summaries in `calibration/summaries.md`. The model must return the strict JSON in section 5 (fit score, flavor, seniority and location checks, 3 reasons, gaps, deal-breakers, comp, one-line summary).

A role passes only if `scoring.threshold` (default 75) is met, `seniority_match` and `location_ok` are true, and no deal-breaker is hit. Raise the threshold in `config.yaml` if the inbox gets noisy.

- **Cache:** results are saved in `state/score_cache.json` by job ID plus a hash of the description, so a role is re-scored only if its text changed.
- **Cap:** `limits.max_roles_scored_per_run` limits real model calls. Cache hits are free. Watchlist companies are scored first, then newest first. Roles over the cap, or whose scoring call failed, are not marked seen and are retried next run. A cap hit prints a `CAP HIT` line.
- **One failure never stops the run.** A failed call is logged and skipped.

## Company vetting

Only roles that passed fit scoring get vetted, and watchlist companies are skipped (the email says "On your watchlist, not vetted."). The model researches the company with web search and reports facts; **the pass / flag / block verdict is then computed in code from the thresholds under `vetting.rules` in `config.yaml`**, so you control the rules. Results are cached 60 days in `state/company_verdicts.json`.

- **Block** (role not emailed): headcount clearly shrinking (medium or high confidence), a layoff of 15% or more of staff in 12 months, a rating below 3.3 with at least 50 reviews, or credible reports of winding down or serious financial trouble. Blocked roles and their reasons are appended to `logs/blocked.jsonl` so you can overrule one.
- **Flag** (emailed with a warning): any smaller layoff, a rating under 3.7, unknown trajectory, low confidence, or no sources. No evidence either way is a flag with low confidence, never a pass.
- **Pass:** growing or stable with no red flags found.
- **Sources** are the URLs from OpenRouter's web search results, never the model's own list.
- **If research fails** for a company, its role is emailed flagged "not checked" and the failure is not cached.
- **TrueUp is not used.** Its terms of service prohibit automated or AI-agent access and its robots.txt blocks AI crawlers, so vetting relies on layoff trackers, news, funding news, and search-result snippets of Glassdoor/Blind/Comparably (no logins, no scraping).
- **These ratings are an input to your judgment, not an authority.** They may be thin or wrong.

Try it by hand: `python vet_company.py "Company Name"`.

## Schedule

Runs every 3 hours at 7am, 10am, 1pm, 4pm, and 7pm Central, every day, and can be started by hand from the Actions tab. (Every 3 hours from 7am lands on 7pm; the next slot would be 10pm, after your 9pm cutoff. Change `run_hours_local` in `config.yaml` to adjust.) GitHub schedules in UTC, so the workflow lists both daylight and standard-time slots and the script skips the one that doesn't match Central time.

## Build status

Scaffold complete. Discovery and the pre-filter are built. Role fit scoring is built. Company vetting is built. Email layout is a stub being filled in step by step.
