"use strict";

const FEED_URL = "feed.json";
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const SEEN_TTL_MS = 48 * 60 * 60 * 1000;
const SEEN_AFTER_MS = 1200; // a story counts as seen after it has been on screen this long
const OTHER = "_other"; // pseudo-topic: stories the labeller could not place
const FIXED_TABS = { foryou: "For you", latest: "Latest", top: "Top", india: "India", world: "World" };

const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage unavailable: run without it */ }
  },
  getJSON(key) {
    try { return JSON.parse(this.get(key)); } catch { return null; }
  },
};

const $ = (id) => document.getElementById(id);
const els = {
  stories: $("stories"),
  tabs: $("tabs"),
  updated: $("updated"),
  refresh: $("refresh"),
  openTopics: $("open-topics"),
  sheet: $("topics-sheet"),
  topicChoices: $("topic-choices"),
  langChoices: $("lang-choices"),
  notice: $("notice"),
  empty: $("empty"),
  foot: $("foot"),
  dot: $("fresh-dot"),
};

let feed = null;
let loading = false;
let lastLoadedAt = 0;
let tabs = [];
let activeTab = store.get("pulse.tab") || "foryou";
let noticeTimer = 0;

/* ---------- Seen tracking (per device) ----------
   Each story carries keys, one per article. A story is unseen until one of its keys was on screen
   for SEEN_AFTER_MS. Ordering and dots are judged against a snapshot taken when you arrive or tap
   refresh, so the list doesn't shuffle under your thumb while you read. */

let seen = loadSeen();
let seenSnapshot = new Set(Object.keys(seen));
const seenTimers = new WeakMap();

function loadSeen() {
  const raw = store.getJSON("pulse.seen") || {};
  const cutoff = Date.now() - SEEN_TTL_MS;
  const kept = {};
  for (const [key, at] of Object.entries(raw)) if (at > cutoff) kept[key] = at;
  return kept;
}

function markSeen(keys) {
  const now = Date.now();
  let changed = false;
  for (const key of keys) {
    if (!seen[key]) {
      seen[key] = now;
      changed = true;
    }
  }
  if (changed) store.set("pulse.seen", JSON.stringify(seen));
}

function refreshSnapshot() {
  seenSnapshot = new Set(Object.keys(seen));
}

function isUnseen(story) {
  const keys = story.keys?.length ? story.keys : [story.id];
  return !keys.some((key) => seenSnapshot.has(key));
}

const observer = "IntersectionObserver" in window
  ? new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const node = entry.target;
          if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
            if (!seenTimers.has(node)) {
              seenTimers.set(node, setTimeout(() => markSeen(JSON.parse(node.dataset.keys)), SEEN_AFTER_MS));
            }
          } else if (seenTimers.has(node)) {
            clearTimeout(seenTimers.get(node));
            seenTimers.delete(node);
          }
        }
      },
      { threshold: [0, 0.6, 1] },
    )
  : null;

/* ---------- Preferences (per device) ---------- */

// Saved as { selected: [...], known: [...] } so topics added to config.toml later
// arrive with their default instead of silently staying off.
function loadChoice(key, options, defaultOn) {
  const saved = store.getJSON(key);
  const ids = options.map((o) => o.id);
  if (!saved || !Array.isArray(saved.selected)) return new Set(ids.filter((id) => defaultOn(id)));
  const known = new Set(saved.known || []);
  const selected = new Set(saved.selected.filter((id) => ids.includes(id)));
  for (const id of ids) if (!known.has(id) && defaultOn(id)) selected.add(id);
  return selected;
}

function saveChoice(key, options, selected) {
  store.set(key, JSON.stringify({ selected: [...selected], known: options.map((o) => o.id) }));
}

function topicOptions() {
  return [...(feed?.topics ?? []), { id: OTHER, label: "Other news", default: true }];
}

function languageOptions() {
  return feed?.languages ?? [{ id: "en", label: "English" }];
}

let selectedTopics = new Set();
let selectedLangs = new Set();

function loadPreferences() {
  const topics = topicOptions();
  selectedTopics = loadChoice("pulse.topics", topics, (id) => topics.find((t) => t.id === id)?.default !== false);
  selectedLangs = loadChoice("pulse.langs", languageOptions(), () => true);
}

/* ---------- Helpers ---------- */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function timeAgo(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

function clock(msOrIso) {
  return new Date(msOrIso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function shortDate(iso) {
  return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
}

function topicLabel(id) {
  return feed?.topics?.find((t) => t.id === id)?.label ?? id;
}

function nextRunMs() {
  if (feed.next_run_at) return Date.parse(feed.next_run_at);
  return Date.parse(feed.checked_at || feed.generated_at) + (feed.interval_minutes || 30) * 60000;
}

/* ---------- Filtering and ordering ---------- */

function languageAllowed(story) {
  const langs = story.langs?.length ? story.langs : [story.lang || "en"];
  return langs.some((l) => selectedLangs.has(l));
}

function matchesTopics(story) {
  if (story.breaking || story.watched) return true;
  if (!story.topics?.length) return selectedTopics.has(OTHER);
  return story.topics.some((t) => selectedTopics.has(t));
}

const byRank = (a, b) => b.rank - a.rank;
const byTime = (a, b) => Date.parse(b.latest) - Date.parse(a.latest);

// Ranked tabs: stories you haven't seen on this device first, then the rest. Each part keeps rank order.
function unseenFirst(list) {
  const fresh = list.filter(isUnseen);
  const rest = list.filter((s) => !isUnseen(s));
  return { fresh, rest };
}

function storiesFor(tab) {
  const all = (feed?.stories ?? []).filter(languageAllowed);
  if (tab === "latest") return { fresh: [...all].sort(byTime), rest: [] };
  if (tab === "india" || tab === "world") return { fresh: all.filter((s) => s.section === tab).sort(byTime), rest: [] };
  if (tab === "top") return unseenFirst([...all].sort(byRank).slice(0, 80));
  if (tab === "foryou") return unseenFirst(all.filter(matchesTopics).sort(byRank).slice(0, 100));
  return unseenFirst(all.filter((s) => s.topics?.includes(tab)).sort(byRank)); // a topic tab
}

/* ---------- Rendering ---------- */

function meter(story) {
  const level = Math.min(5, Math.max(1, story.independent));
  const heat = story.score >= feed.thresholds.alert ? 2 : story.hot ? 1 : 0;
  const label = `Covered by ${story.outlets} outlet${story.outlets === 1 ? "" : "s"}`;
  const node = el("span", { class: `meter heat-${heat}`, role: "img", "aria-label": label, title: label });
  for (let i = 0; i < 5; i += 1) node.append(el("i", { class: i < level ? "on" : null }));
  return node;
}

function linkOrText(cls, url, text, lang) {
  const href = safeUrl(url);
  const attrs = { class: cls || null, lang: lang && lang !== "en" ? lang : null };
  return href
    ? el("a", { ...attrs, href, target: "_blank", rel: "noopener noreferrer" }, text)
    : el("span", attrs, text);
}

// Why this story did or didn't reach your phone, in one line.
function alertReason(story) {
  if (story.alerted) return "Notified to your phone.";
  const needs = story.needs || { score: feed.thresholds.alert, outlets: feed.thresholds.min_outlets ?? 3, eligible: true };
  const parts = [
    `Score ${story.score} of ${needs.score} needed`,
    `${story.independent} independent outlet${story.independent === 1 ? "" : "s"} of ${needs.outlets} needed`,
  ];
  if (!needs.eligible) parts.push("topic not on the alert list");
  return `No ping: ${parts.join(", ")}.`;
}

function storyItem(story) {
  const unseen = isUnseen(story);
  const tags = [
    ...story.categories.map((label) => el("span", { class: "tag tag-sensitive" }, label)),
    story.unconfirmed && el("span", { class: "tag tag-warn" }, "Single source"),
    story.watched && el("span", { class: "tag tag-watch" }, "Watching"),
    story.section === "world" && story.india && el("span", { class: "tag" }, "India angle"),
    story.alerted && el("span", { class: "tag" }, "Notified"),
  ].filter(Boolean);

  const meta = el(
    "p",
    { class: "meta" },
    el("span", { class: "outlet" }, story.outlet),
    el("time", { datetime: story.latest }, timeAgo(story.latest)),
    story.topics?.length ? el("span", { class: "topics" }, story.topics.map(topicLabel).join(", ")) : null,
  );

  const body = el("div", { class: "body" }, linkOrText("headline", story.link, story.title, story.lang), meta);
  if (tags.length) body.append(el("p", { class: "tags" }, tags));

  const others = story.coverage.length;
  const details = el(
    "details",
    { class: "more" },
    el("summary", {}, others ? `Also covered by ${others} other outlet${others === 1 ? "" : "s"}` : "Only one outlet so far"),
    el("p", { class: "why" }, alertReason(story)),
  );
  if (others) {
    details.append(
      el(
        "ul",
        { class: "coverage" },
        story.coverage.map((c) =>
          el("li", {}, el("span", { class: "outlet" }, c.outlet), linkOrText("", c.link, c.title, c.lang)),
        ),
      ),
    );
  }
  body.append(details);

  const classes = ["story", story.hot && "is-hot", unseen && "is-new"].filter(Boolean).join(" ");
  const item = el("li", { class: classes, "data-keys": JSON.stringify(story.keys?.length ? story.keys : [story.id]) }, meter(story), body);
  if (observer) observer.observe(item);
  return item;
}

function buildTabs() {
  const wanted = feed.tabs?.length ? feed.tabs : ["foryou", "top", "india", "world"];
  tabs = wanted
    .filter((id) => FIXED_TABS[id] || feed.topics?.some((t) => t.id === id))
    .map((id) => ({ id, label: FIXED_TABS[id] ?? topicLabel(id) }));
  if (!tabs.length) tabs = [{ id: "latest", label: "Latest" }];
  if (!tabs.some((t) => t.id === activeTab)) activeTab = tabs[0].id;
}

function renderTabs() {
  els.tabs.replaceChildren(
    ...tabs.map((tab) => {
      const button = el(
        "button",
        {
          type: "button",
          class: "tab",
          role: "tab",
          id: `tab-${tab.id}`,
          "aria-selected": String(tab.id === activeTab),
          "aria-controls": "stories",
        },
        tab.label,
      );
      button.addEventListener("click", () => {
        if (activeTab === tab.id) return;
        activeTab = tab.id;
        store.set("pulse.tab", tab.id);
        refreshSnapshot();
        renderTabs();
        renderStories();
        window.scrollTo({ top: 0 });
      });
      return button;
    }),
  );
  els.stories.setAttribute("aria-labelledby", `tab-${activeTab}`);
}

function renderStories() {
  if (!feed) return;
  if (observer) observer.disconnect();
  const { fresh, rest } = storiesFor(activeTab);
  const nodes = fresh.map(storyItem);
  if (rest.length) {
    nodes.push(el("li", { class: "divider", role: "presentation" }, fresh.length ? "Seen earlier" : "Nothing new; seen earlier"));
    nodes.push(...rest.map(storyItem));
  }
  els.stories.replaceChildren(...nodes);
  els.empty.hidden = nodes.length > 0;
  if (nodes.length) return;
  if (activeTab === "foryou") {
    const pick = el("button", { type: "button", class: "link-button" }, "Choose topics");
    pick.addEventListener("click", openSheet);
    els.empty.replaceChildren("Nothing in your topics right now. ", pick);
  } else {
    els.empty.textContent = `Nothing here in the last 24 hours. The next update runs around ${clock(nextRunMs())}.`;
  }
}

function renderHeader() {
  if (!feed) return;
  const ageMin = (Date.now() - Date.parse(feed.checked_at || feed.generated_at)) / 60000;
  const interval = feed.interval_minutes || 30;
  els.updated.textContent = `Updated ${timeAgo(feed.generated_at)}`;
  els.dot.className = `dot ${ageMin <= interval * 1.5 ? "is-fresh" : ageMin <= interval * 4 ? "is-stale" : "is-old"}`;
  for (const node of els.stories.querySelectorAll("time[datetime]")) {
    node.textContent = timeAgo(node.getAttribute("datetime"));
  }
}

function renderFoot() {
  if (!feed) return;
  const failing = feed.sources.filter((s) => !s.ok);
  const okCount = feed.sources.length - failing.length;
  const summary = `${okCount} of ${feed.sources.length} sources responding`;
  const describe = (s) => `${s.name}: ${s.error}${s.failing_since ? `, failing since ${shortDate(s.failing_since)}` : ""}`;
  const cadence = feed.interval_minutes ? ` Runs every ${feed.interval_minutes} minutes.` : "";
  els.foot.replaceChildren(
    el("p", {}, `Updated at ${clock(feed.generated_at)}. Next update around ${clock(nextRunMs())}.${cadence}`),
    failing.length
      ? el("details", {}, el("summary", {}, summary), el("ul", {}, failing.map((s) => el("li", {}, describe(s)))))
      : el("p", {}, summary),
  );
}

function renderAll() {
  buildTabs();
  renderTabs();
  renderStories();
  renderHeader();
  renderFoot();
  if (feed?.stale) {
    showNotice(`Most sources are unreachable right now. Showing the last good update from ${clock(feed.generated_at)}.`);
  }
}

function showNotice(text, ms = 0) {
  clearTimeout(noticeTimer);
  els.notice.hidden = !text;
  els.notice.textContent = text || "";
  if (text && ms) noticeTimer = setTimeout(() => showNotice(""), ms);
}

/* ---------- Topic picker ---------- */

function choiceList(container, options, selected, countFor, onChange) {
  const legend = container.querySelector("legend");
  container.replaceChildren(legend);
  for (const option of options) {
    const id = `choice-${container.id}-${option.id}`;
    const input = el("input", { type: "checkbox", id, value: option.id, checked: selected.has(option.id) });
    input.addEventListener("change", () => {
      if (input.checked) selected.add(option.id);
      else selected.delete(option.id);
      onChange();
    });
    const count = countFor(option.id);
    container.append(
      el(
        "label",
        { class: "choice", for: id },
        input,
        el("span", { class: "choice-label", lang: option.id.length === 2 && option.id !== "en" ? option.id : null }, option.label),
        el("span", { class: "choice-count" }, count ? String(count) : ""),
      ),
    );
  }
}

function renderSheet() {
  const stories = feed?.stories ?? [];
  const topicCount = (id) =>
    id === OTHER ? stories.filter((s) => !s.topics?.length).length : stories.filter((s) => s.topics?.includes(id)).length;
  const langCount = (id) => stories.filter((s) => (s.langs ?? [s.lang]).includes(id)).length;

  choiceList(els.topicChoices, topicOptions(), selectedTopics, topicCount, () => {
    saveChoice("pulse.topics", topicOptions(), selectedTopics);
    renderStories();
  });
  choiceList(els.langChoices, languageOptions(), selectedLangs, langCount, () => {
    saveChoice("pulse.langs", languageOptions(), selectedLangs);
    renderStories();
  });
}

function openSheet() {
  renderSheet();
  if (typeof els.sheet.showModal === "function") els.sheet.showModal();
  else els.sheet.setAttribute("open", "");
}

els.openTopics.addEventListener("click", openSheet);
els.sheet.addEventListener("click", (event) => {
  if (event.target === els.sheet) els.sheet.close(); // tap outside the sheet
});

/* ---------- Loading ---------- */

async function load(manual = false) {
  if (loading) return;
  loading = true;
  els.refresh.classList.add("is-loading");
  els.refresh.disabled = true;
  const before = feed?.generated_at;
  try {
    const response = await fetch(`${FEED_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const firstLoad = !feed;
    feed = await response.json();
    lastLoadedAt = Date.now();
    if (firstLoad) loadPreferences();
    if (manual) refreshSnapshot();
    showNotice("");
    renderAll();
    if (manual && before && before === feed.generated_at && !feed.stale) {
      // The button reloads the page's data; it cannot run the pipeline. Say when the next build is due.
      showNotice(`No new build since ${clock(feed.generated_at)}. The next run is around ${clock(nextRunMs())}.`, 6000);
    }
  } catch (error) {
    showNotice(
      feed
        ? "Couldn't reach the feed. Showing the last copy loaded on this device."
        : "Couldn't load the feed. Check your connection, then tap refresh.",
    );
  } finally {
    loading = false;
    els.refresh.classList.remove("is-loading");
    els.refresh.disabled = false;
  }
}

els.refresh.addEventListener("click", () => load(true));

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - lastLoadedAt > 60 * 1000) {
    refreshSnapshot();
    load();
  }
});

setInterval(() => {
  if (document.visibilityState === "visible") load();
}, AUTO_REFRESH_MS);
setInterval(renderHeader, 60 * 1000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline support is optional */ });
}

load();
