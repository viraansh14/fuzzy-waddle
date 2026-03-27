# fuzzy-waddle

Fully automated job application tool. Searches LinkedIn, Indeed, Glassdoor, and
arbitrary job posting URLs, then fills and submits application forms — including
questionnaires — using Claude AI to answer questions based on your CV.

## How it works

1. Parses your CV (PDF or DOCX) and extracts structured data via Claude AI.
2. Searches enabled platforms for matching job listings.
3. For each listing, opens the application form and fills every field:
   - Contact info (name, email, phone) is filled directly from your CV.
   - File upload fields receive your CV file.
   - All other questions (experience, eligibility, cover letter, etc.) are
     answered by Claude with your CV as context.
4. Submits the application and logs the result.

## Setup

### 1. Install dependencies

```bash
pip install -e .
playwright install chromium
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- Set your Anthropic API key (or export `ANTHROPIC_API_KEY`)
- Set the path to your CV file
- Configure job titles, locations, and which platforms to enable

### 3. Log in to job platforms (one-time)

```bash
job-applier login linkedin
job-applier login indeed
```

This opens a browser window. Log in manually, then press Enter in the terminal.
Your session cookies are saved for future runs.

### 4. Verify CV parsing

```bash
job-applier parse-cv ~/Documents/my_resume.pdf
```

Review the extracted structured data before running the full tool.

### 5. Run

```bash
# Dry run first (fills forms but doesn't submit)
job-applier apply --dry-run

# Real run
job-applier apply

# Limit platforms or number of applications
job-applier apply --platform linkedin --limit 5
```

## Commands

| Command | Description |
|---|---|
| `job-applier apply` | Run the application loop |
| `job-applier login <platform>` | Authenticate with a platform |
| `job-applier parse-cv <file>` | Preview CV extraction |
| `job-applier status` | Show application history |

## Options for `apply`

| Flag | Description |
|---|---|
| `--config` / `-c` | Path to config file (default: `config.yaml`) |
| `--cv` / `-r` | Override CV path from config |
| `--platform` / `-p` | Run only one platform |
| `--dry-run` | Fill forms but do not submit |
| `--limit` / `-n` | Override `max_applications_per_run` |

## Data files

| File | Description |
|---|---|
| `data/applied_jobs.json` | IDs of every applied job (prevents re-applying) |
| `data/logs/applications.jsonl` | Structured log of every application attempt |
| `data/cookies/<platform>.json` | Saved authentication cookies |

## Notes

- Automated applications may violate platform Terms of Service. Use responsibly.
- Always run with `--dry-run` first to verify behaviour.
- Keep `max_applications_per_run` conservative (≤ 25/day) to avoid account flags.
- Re-run `job-applier login <platform>` if you get authentication errors.