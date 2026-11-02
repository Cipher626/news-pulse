"use strict";

const FEED_URL = "feed.json";
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const OTHER = "_other"; // pseudo-topic: stories the labeller could not place
const TABS = [
  { id: "foryou", label: "For you" },
  { id: "top", label: "Top" },
  { id: "india", label: "India" },
  { id: "world", label: "World" },
];

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
let activeTab = TABS.some((t) => t.id === store.get("pulse.tab")) ? store.get("pulse.tab") : "foryou";
// Stories first seen after this moment get a "new" dot. 0 on the very first visit: nothing is new yet.
let lastVisit = Number(store.get("pulse.lastVisit")) || 0;

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

/* ---------- Filtering ---------- */

function languageAllowed(story) {
  const langs = story.langs?.length ? story.langs : [story.lang || "en"];
  return langs.some((l) => selectedLangs.has(l));
}

function matchesTopics(story) {
  if (story.breaking || story.watched) return true;
  if (!story.topics?.length) return selectedTopics.has(OTHER);
  return story.topics.some((t) => selectedTopics.has(t));
}

function storiesFor(tab) {
  const all = (feed?.stories ?? []).filter(languageAllowed);
  const byRank = (a, b) => b.rank - a.rank;
  const byTime = (a, b) => Date.parse(b.latest) - Date.parse(a.latest);
  if (tab === "foryou") return all.filter(matchesTopics).sort(byRank).slice(0, 80);
  if (tab === "top") return [...all].sort(byRank).slice(0, 60);
  return all.filter((s) => s.section === tab).sort(byTime);
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

function storyItem(story) {
  const isNew = lastVisit > 0 && Date.parse(story.first_seen) > lastVisit;

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

  if (story.coverage.length) {
    const others = story.coverage.length;
    body.append(
      el(
        "details",
        { class: "more" },
        el("summary", {}, `Also covered by ${others} other outlet${others === 1 ? "" : "s"}`),
        el(
          "ul",
          { class: "coverage" },
          story.coverage.map((c) =>
            el("li", {}, el("span", { class: "outlet" }, c.outlet), linkOrText("", c.link, c.title, c.lang)),
          ),
        ),
      ),
    );
  }

  const classes = ["story", story.hot && "is-hot", isNew && "is-new"].filter(Boolean).join(" ");
  return el("li", { class: classes }, meter(story), body);
}

function renderTabs() {
  els.tabs.replaceChildren(
    ...TABS.map((tab) => {
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
  const list = storiesFor(activeTab);
  els.stories.replaceChildren(...list.map(storyItem));
  els.empty.hidden = list.length > 0;
  if (list.length) return;
  if (activeTab === "foryou") {
    const pick = el("button", { type: "button", class: "link-button" }, "Choose topics");
    pick.addEventListener("click", openSheet);
    els.empty.replaceChildren("Nothing in your topics right now. ", pick);
  } else {
    const next = Date.parse(feed.generated_at) + feed.interval_minutes * 60000;
    els.empty.textContent = `Nothing here in the last 24 hours. The next update runs around ${clock(next)}.`;
  }
}

function renderHeader() {
  if (!feed) return;
  const ageMin = (Date.now() - Date.parse(feed.generated_at)) / 60000;
  els.updated.textContent = `Updated ${timeAgo(feed.generated_at)}`;
  els.dot.className = `dot ${ageMin <= feed.interval_minutes * 1.5 ? "is-fresh" : ageMin <= 120 ? "is-stale" : "is-old"}`;
  for (const node of els.stories.querySelectorAll("time[datetime]")) {
    node.textContent = timeAgo(node.getAttribute("datetime"));
  }
}

function renderFoot() {
  if (!feed) return;
  const failing = feed.sources.filter((s) => !s.ok);
  const okCount = feed.sources.length - failing.length;
  const next = Date.parse(feed.checked_at || feed.generated_at) + feed.interval_minutes * 60000;
  const summary = `${okCount} of ${feed.sources.length} sources responding`;
  const describe = (s) => `${s.name}: ${s.error}${s.failing_since ? `, failing since ${shortDate(s.failing_since)}` : ""}`;
  els.foot.replaceChildren(
    el("p", {}, `Updated at ${clock(feed.generated_at)}. Next update around ${clock(next)}.`),
    failing.length
      ? el("details", {}, el("summary", {}, summary), el("ul", {}, failing.map((s) => el("li", {}, describe(s)))))
      : el("p", {}, summary),
  );
}

function renderAll() {
  renderStories();
  renderHeader();
  renderFoot();
  if (feed?.stale) {
    showNotice(`Most sources are unreachable right now. Showing the last good update from ${clock(feed.generated_at)}.`);
  }
}

function showNotice(text) {
  els.notice.hidden = !text;
  els.notice.textContent = text || "";
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

async function load() {
  if (loading) return;
  loading = true;
  els.refresh.classList.add("is-loading");
  els.refresh.disabled = true;
  try {
    const response = await fetch(`${FEED_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const firstLoad = !feed;
    feed = await response.json();
    lastLoadedAt = Date.now();
    if (firstLoad) loadPreferences();
    showNotice("");
    renderAll();
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

els.refresh.addEventListener("click", load);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    store.set("pulse.lastVisit", String(Date.now()));
  } else {
    lastVisit = Number(store.get("pulse.lastVisit")) || lastVisit;
    if (Date.now() - lastLoadedAt > 60 * 1000) load();
  }
});

setInterval(() => {
  if (document.visibilityState === "visible") load();
}, AUTO_REFRESH_MS);
setInterval(renderHeader, 60 * 1000);

if (!lastVisit) store.set("pulse.lastVisit", String(Date.now()));

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline support is optional */ });
}

renderTabs();
load();
