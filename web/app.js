"use strict";

const FEED_URL = "feed.json";
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const TABS = [
  { id: "top", label: "Top" },
  { id: "india", label: "India" },
  { id: "world", label: "World" },
  { id: "business", label: "Business" },
];

const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage unavailable: run without it */ }
  },
};

const $ = (id) => document.getElementById(id);
const els = {
  stories: $("stories"),
  tabs: $("tabs"),
  updated: $("updated"),
  refresh: $("refresh"),
  notice: $("notice"),
  empty: $("empty"),
  foot: $("foot"),
  dot: $("fresh-dot"),
};

let feed = null;
let loading = false;
let lastLoadedAt = 0;
let activeTab = TABS.some((t) => t.id === store.get("pulse.tab")) ? store.get("pulse.tab") : "top";
// Stories first seen after this moment get a "new" dot. 0 on the very first visit: nothing is new yet.
let lastVisit = Number(store.get("pulse.lastVisit")) || 0;

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

function storiesFor(tab) {
  const all = feed?.stories ?? [];
  if (tab === "top") return [...all].sort((a, b) => b.rank - a.rank).slice(0, 60);
  return all
    .filter((s) => s.section === tab)
    .sort((a, b) => Date.parse(b.latest) - Date.parse(a.latest));
}

function meter(story) {
  const level = Math.min(5, Math.max(1, story.independent));
  const heat = story.score >= feed.thresholds.alert ? 2 : story.hot ? 1 : 0;
  const label = `Covered by ${story.outlets} outlet${story.outlets === 1 ? "" : "s"}`;
  const node = el("span", { class: `meter heat-${heat}`, role: "img", "aria-label": label, title: label });
  for (let i = 0; i < 5; i += 1) node.append(el("i", { class: i < level ? "on" : null }));
  return node;
}

function linkOrText(cls, url, text) {
  const href = safeUrl(url);
  return href
    ? el("a", { class: cls, href, target: "_blank", rel: "noopener noreferrer" }, text)
    : el("span", { class: cls }, text);
}

function storyItem(story) {
  const isNew = lastVisit > 0 && Date.parse(story.first_seen) > lastVisit;

  const tags = [
    ...story.categories.map((label) => el("span", { class: "tag tag-sensitive" }, label)),
    story.unconfirmed && el("span", { class: "tag tag-warn" }, "Single source"),
    story.section === "world" && story.india && el("span", { class: "tag" }, "India angle"),
    story.alerted && el("span", { class: "tag" }, "Notified"),
  ].filter(Boolean);

  const body = el(
    "div",
    { class: "body" },
    linkOrText("headline", story.link, story.title),
    el(
      "p",
      { class: "meta" },
      el("span", { class: "outlet" }, story.outlet),
      el("time", { datetime: story.latest }, timeAgo(story.latest)),
    ),
  );
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
            el("li", {}, el("span", { class: "outlet" }, c.outlet), linkOrText("", c.link, c.title)),
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
  if (!list.length) {
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
  const next = Date.parse(feed.generated_at) + feed.interval_minutes * 60000;
  const summary = `${okCount} of ${feed.sources.length} sources responding`;
  els.foot.replaceChildren(
    el("p", {}, `Updated at ${clock(feed.generated_at)}. Next update around ${clock(next)}.`),
    failing.length
      ? el(
          "details",
          {},
          el("summary", {}, summary),
          el("ul", {}, failing.map((s) => el("li", {}, `${s.name}: ${s.error}`))),
        )
      : el("p", {}, summary),
  );
}

function showNotice(text) {
  els.notice.hidden = !text;
  els.notice.textContent = text || "";
}

async function load() {
  if (loading) return;
  loading = true;
  els.refresh.classList.add("is-loading");
  els.refresh.disabled = true;
  try {
    const response = await fetch(`${FEED_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    feed = await response.json();
    lastLoadedAt = Date.now();
    showNotice("");
    renderStories();
    renderHeader();
    renderFoot();
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
