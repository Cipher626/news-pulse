# Pulse

A personal news radar. India first, the world when it matters.

Every 30 minutes a GitHub Action pulls ~20 RSS feeds, groups headlines about the same event into one story, scores how widely each story is spreading, publishes a phone-friendly web app, and pushes a notification when something crosses your alert threshold.

```
GitHub Actions (cron, every 30 min)
  └─ python -m pipeline.run
       fetch.py    RSS feeds → articles
       cluster.py  articles → stories (headline word overlap)
       score.py    stories → score (independent outlets + freshness + sensitive topic)
       alerts.py   which stories notify (thresholds, quiet hours, daily cap, no repeats)
       notify.py   ntfy push → your phone
       run.py      writes site/feed.json, saves data/state.json
  └─ GitHub Pages serves site/ (installable web app)
```

Cost: zero. No servers, no API keys.

## One-time setup (about 15 minutes)

### 1. Put the code on GitHub

Create a **public** repository named `news-pulse` (GitHub Pages on a free account needs a public repo; with GitHub Pro from the Student Developer Pack a private repo also works). Then, from this folder:

```bash
git init -b main
git add .
git commit -m "Pulse v1"
git remote add origin https://github.com/YOUR_USERNAME/news-pulse.git
git push -u origin main
```

Use the command line, not drag-and-drop upload: the web uploader can skip the hidden `.github` folder.

### 2. Turn on Pages

Repository → **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 3. Set up notifications

1. Install **ntfy** on your phone (Play Store or App Store).
2. Make up a hard-to-guess topic name. Anyone who knows it can read and send to it, so treat it like a password:
   ```bash
   python -c "import secrets; print('pulse-' + secrets.token_urlsafe(12))"
   ```
3. In the ntfy app: **+ → Subscribe to topic →** paste the name, server `ntfy.sh`.
4. On GitHub: **Settings → Secrets and variables → Actions → New repository secret**. Name `NTFY_TOPIC`, value the same topic name.

### 4. First run

**Actions** tab → **news** → **Run workflow** → tick **Send a test notification** → **Run workflow**. It takes 1–2 minutes. Your phone should buzz with "Pulse is connected".

The very first run deliberately does not notify you about stories that are already big. It remembers them so you don't get a burst of old news. Real alerts start from the next run.

### 5. Put it on your home screen

Open `https://YOUR_USERNAME.github.io/news-pulse/`.

- **Android (Chrome):** menu ⋮ → **Add to Home screen** (or **Install app**).
- **iPhone (Safari):** Share → **Add to Home Screen**.
- **Laptop:** Chrome or Edge shows an install icon in the address bar; or just bookmark it.

## Reading the page

- **Bars to the left of each headline**: how many independent outlets carry the story, up to five. Grey is normal, saffron is hot, red has reached alert level.
- **Tags**: Disaster, Violence, Conflict, Markets, Politics, Health come from keyword lists. **Single source** means a sensitive story only one outlet has reported so far: treat it as unconfirmed. **India angle** marks a world story that involves India. **Notified** means it was pushed to your phone.
- **Dot before a headline**: new since you last opened the app.
- **Faded headline**: you already opened it.
- **Footer**: when the feed was built, roughly when the next build runs, and which sources failed.

## Changing things

| I want to… | Edit |
|---|---|
| Add or remove a source | `config.toml` → `[[feeds]]` |
| Fewer or more notifications | `config.toml` → `[alerts]` `score`, `min_outlets`, `max_per_day` |
| Change quiet hours | `config.toml` → `[alerts]` `quiet_start`, `quiet_end` (IST) |
| Add a topic that counts as sensitive | `config.toml` → new `[[sensitive]]` block |
| Change how often it runs | `cron` line in `.github/workflows/news.yml` (GitHub's minimum is every 5 min) |
| Better story grouping | `pipeline/cluster.py`; keep the `cluster(articles, settings)` signature |
| Telegram/email instead of ntfy | `pipeline/notify.py` only |
| A news API or search instead of RSS | `pipeline/fetch.py`: return `list[Article]`, call it in `run.py` |
| The page design | `web/`; bump `VERSION` in `web/sw.js` so phones pick up the change |

Pushing any of these files triggers a run, so changes go live in a couple of minutes.

## Test before you push

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m tests.smoke_test          # offline, fake feeds, checks grouping/scoring/alerts
python -m pipeline.run --dry-run    # real feeds; prints would-be alerts; sends nothing; state untouched
python -m http.server -d site 8000  # then open http://localhost:8000
```

## Known limits of v1

- **Grouping is headline word overlap.** It misses synonyms ("century" vs "hundred", "SC" vs "Supreme Court"), so one story can show up twice with lower counts each. That errs toward fewer alerts rather than wrong ones. The upgrade is embeddings or an LLM pass in `cluster.py`.
- **"How many people read it" is not measured.** No free source publishes readership. The proxy is how many independent outlets cover a story and how recently.
- **Wire copy is caught only when headlines are near-identical.** Outlets that rewrite a PTI headline count as independent.
- **GitHub's schedule is best-effort.** Runs can start late, and at busy times one can be skipped.
- **Some outlets block cloud servers.** Failing feeds show in the page footer; replace or delete them in `config.toml`.
- **The page is public** (headlines and links only). Your ntfy topic stays secret in repository secrets.
- **One small state commit per run** (~48 a day). It keeps alert memory and keeps the repository active so GitHub doesn't pause the schedule. Fine for months; squash later if the history bothers you.

## Troubleshooting

- **Save state step fails with 403**: Settings → Actions → General → Workflow permissions → **Read and write permissions**.
- **Deploy step says Pages isn't enabled**: redo setup step 2.
- **Page is empty**: open the latest run in the Actions tab and read the "Build feed" step; it lists every skipped source and why.
- **No test notification**: the secret must be named exactly `NTFY_TOPIC`, and the phone must be subscribed to the identical topic on `ntfy.sh`.
- **Phone shows an old version of the page**: close and reopen the app; if that fails, bump `VERSION` in `web/sw.js` and push.
