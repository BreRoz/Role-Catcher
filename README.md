# Role Catcher

Watches the careers pages of a fixed list of companies, scores every newly posted
role against your background, and emails you **only** roles that score **80 or higher**.
It does not write cover letters and does not apply for you. You apply yourself.

It runs on GitHub Actions (free for this size of job), so your laptop doesn't need to be on.
Schedule: 7am, 10am, 1pm, 4pm and 7pm Central, every day. You can also run it by hand.

## What's in this folder

| Path | What it is |
|---|---|
| `companies.yaml` | The companies to watch |
| `profile.md` | Your background and the scoring rules |
| `calibration/` | Example roles that teach the scorer your taste |
| `role_catcher/` | The program itself |
| `state/seen_jobs.json` | Remembers roles already seen, so you never get one twice |
| `.github/workflows/role-catcher.yml` | The schedule |

## One-time setup

1. **Put this folder on GitHub.** Create a new *private* repository at github.com and upload
   this folder's contents to it.
2. **Make a Gmail app password.** Turn on 2-Step Verification for your Google account, then go to
   myaccount.google.com/apppasswords and create one named "Role Catcher". Copy the 16 characters.
3. **Get an OpenRouter API key** at openrouter.ai (Keys).
4. **Add four secrets** in your GitHub repo: Settings, Secrets and variables, Actions, New repository secret.

   | Name | Value |
   |---|---|
   | `OPENROUTER_API_KEY` | your OpenRouter key |
   | `SMTP_USER` | your Gmail address (the one that made the app password) |
   | `SMTP_APP_PASSWORD` | the 16-character app password |
   | `EMAIL_TO` | where alerts go (bre999@gmail.com) |

5. **Allow the workflow to save state.** Settings, Actions, General, Workflow permissions:
   choose *Read and write permissions*, then Save.
6. **Test it.** Actions tab, "Role Catcher", Run workflow.

## Day-to-day

- Add or pause companies by editing `companies.yaml`.
- Change what counts as a good fit by editing `profile.md`.
- Check the Actions tab if you ever suspect it stopped; failed runs show a red mark.

## Running locally (optional)

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m role_catcher.main --dry-run
```
