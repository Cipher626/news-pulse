# Pulse

A personal news radar. India first, the world when it matters.

Every 30 minutes a GitHub Action pulls about 40 RSS feeds in English, Hindi and Gujarati, groups headlines about the same event into one story, labels each story with topics, scores how widely it is spreading, publishes a phone-friendly web app, and pushes a notification when something you care about crosses your alert threshold.

```
GitHub Actions (cron, every 30 min)
  └─ python -m pipeline.run
       fetch.py    RSS feeds → articles
       cluster.py  articles → stories (headline word overlap, English/Hindi/Gujarati + URL words)
       topics.py   stories → topics (feed, URL segment, RSS category, headline keywords)
       score.py    stories → score (independent newsrooms + freshness + sensitive topic)
       alerts.py   which stories notify (thresholds, topics, watch list, quiet hours, caps, no repeats)
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

## Upgrading from v1

Replace every file with this version **except `data/state.json`**, then commit and push. `config.toml` is new: if you had edited the old one, copy your changes across by hand. The page updates itself on phones once the new service worker loads (close and reopen the app if it doesn't).

## Reading the page

- **For you** shows the topics you pick, plus breaking news and anything on your watch list. **Top** is everything ranked by spread and freshness. **India** and **World** are everything in that region, newest first.
- **Topics button** (the sliders next to refresh): choose topics and languages. The number beside each is how many stories it has right now. Choices are saved on that device, so phone and laptop can differ.

- **Bars to the left of each headline**: how many independent outlets carry the story, up to five. Grey is normal, saffron is hot, red has reached alert level.
- **Grey text after the time**: the story's topics.
- **Tags**: Disaster, Violence, Conflict, Markets, Politics, Health come from keyword lists. **Single source** means a sensitive story only one outlet has reported so far: treat it as unconfirmed. **Watching** means it matched your watch list. **India angle** marks a world story that involves India. **Notified** means it was pushed to your phone.
- **Dot before a headline**: new since you last opened the app.
- **Faded headline**: you already opened it.
- **Footer**: when the feed was built, roughly when the next build runs, and which sources failed.

## Changing things

| I want to… | Edit |
|---|---|
| Add or remove a source | `config.toml` → `[[feeds]]` |
| Add a topic to the picker | `config.toml` → new `[[topics]]` block (id, label, paths, keywords) |
| Choose which topics notify | `config.toml` → `[alerts]` `topics` |
| Follow specific names, companies, exams | `config.toml` → `[watch]` `keywords` |
| Fewer or more notifications | `config.toml` → `[alerts]` `score`, `min_outlets`, `max_per_day` |
| Mark two outlets as one newsroom | `config.toml` → `[owners]` |
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

## Known limits

- **Grouping is word overlap.** It misses synonyms ("century" vs "hundred"), so one event can appear as two stories with lower counts each. That errs toward fewer alerts, not wrong ones.
- **Hindi and Gujarati stories join English coverage only through URL words.** Aaj Tak, India TV and Divya Bhaskar put English words in their links, which is what makes the match. Stories that arrive through Google News links (ABP News, Divya Bhaskar's main stream) have no readable URL, so they group only with other Hindi or Gujarati headlines. Multilingual embeddings in `cluster.py` would fix both this and synonyms.
- **Hindi and Gujarati keyword lists are short.** Topics for those stories come mostly from URL sections and feed defaults; add words to `[[topics]]` and `[[sensitive]]` as you notice misses.
- **Four outlets come through Google News search** (India TV English, ABP News Hindi, Divya Bhaskar's general stream, Moneycontrol) because their own feeds are missing, unchecked or blocked. Those links go through a Google redirect, and Google indexes with some delay.
- **Your topic picks don't reach notifications.** The page is static, so a tap on your phone can't change what GitHub Actions does without storing a GitHub token on the device. Notification topics live in `config.toml`.
- **"How many people read it" is not measured.** No free source publishes readership. The proxy is how many independent newsrooms cover a story and how recently.
- **Wire copy is caught only when headlines are near-identical.** Outlets that rewrite a PTI headline count as independent.
- **GitHub's schedule is best-effort.** Runs can start late, and at busy times one can be skipped.
- **The page is public** (headlines and links only). Your ntfy topic stays secret in repository secrets.
- **One small state commit per run.** It keeps alert memory and feed health, and keeps the repository active so GitHub doesn't pause the schedule.

## Troubleshooting

- **Save state step fails with 403**: Settings → Actions → General → Workflow permissions → **Read and write permissions**.
- **Deploy step says Pages isn't enabled**: redo setup step 2.
- **Page is empty**: open the latest run in the Actions tab and read the "Build feed" step; it lists every skipped source and why.
- **Banner says most sources are unreachable**: fewer than `[fetch] min_sources_ok` feeds answered, so the last good page was kept and no alerts went out. It clears on the next healthy run.
- **A source shows "failing since"**: it has failed several runs in a row. Replace its URL or delete its block in `config.toml`.
- **For you is empty**: open the topic picker; you may have unticked everything, or the languages you kept have no stories right now.
- **No test notification**: the secret must be named exactly `NTFY_TOPIC`, and the phone must be subscribed to the identical topic on `ntfy.sh`.
- **Phone shows an old version of the page**: close and reopen the app; if that fails, bump `VERSION` in `web/sw.js` and push.
