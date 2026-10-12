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
| `profile.md` | My background and how roles get scored |
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

## Schedule

Runs every 3 hours at 7am, 10am, 1pm, 4pm, and 7pm Central, every day, and can be started by hand from the Actions tab. (Every 3 hours from 7am lands on 7pm; the next slot would be 10pm, after your 9pm cutoff. Change `run_hours_local` in `config.yaml` to adjust.) GitHub schedules in UTC, so the workflow lists both daylight and standard-time slots and the script skips the one that doesn't match Central time.

## Build status

Scaffold complete. Discovery, filters, scoring, vetting, and email layout are stubs being filled in step by step.
