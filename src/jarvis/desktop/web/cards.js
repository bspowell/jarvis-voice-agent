/* Jarvis's cards: what he has just looked up, beside the orb.

   The desktop app (card_panel.py) calls:
   - cards.put(card): add a card, or update the one with the same id (cards.py writes them)
   - cards.update({awake, state}): the conversation; cards fade 10 s after it ends
   - cards.clear(): every card goes ("clear the cards", or "stop")
   - cards.place({side, align}): which side of the cards the orb is on, which edge they line up with
   - cards.hover(id): the pointer is over a link or button. The panel is never the key window
     (it never takes your keyboard), so WebKit's own hover doesn't fire there.

   The page tells the app (window.webkit.messageHandlers.jarvis) which cards are showing and where
   their links and buttons are, so the panel takes clicks there and nowhere else; and what you
   clicked. Text is always set as text, never as HTML: titles come from web pages. */
"use strict";

const FADE_AFTER_MS = 10_000; // after the conversation ends
const HOVER_GRACE_MS = 3_000; // the pointer is on a card when it's due to fade: a little longer
const DONE_MS = 4_000; // "Signed in." stays this long
const WAITING_MS = 5 * 60_000; // a sign-in card waits this long for you
const SETTLE_MS = 800; // arrival animations are over by then: report where things ended up
const WAITING = new Set(["sign_in_needed", "signing_in", "sign_in_failed"]); // they wait for you

const stack = document.getElementById("stack");
const timers = new Map(); // a sign-in card's own timer, by card id
let awake = false;
let fadeTimer = 0;
let hovered = null;

// --- Talking to the desktop app ---
function tell(message) {
  const app = window.webkit?.messageHandlers?.jarvis;
  if (app) app.postMessage(JSON.stringify(message));
  else if (document.body.classList.contains("demo")) console.log("to the app:", message);
}

let reportDue = 0;
function report() {
  // Soon, not at once: several changes in a row make one report. (Not requestAnimationFrame: a
  // hidden panel draws no frames, and the report must still go.)
  clearTimeout(reportDue);
  reportDue = setTimeout(() => {
    const hotspots = [...stack.querySelectorAll(".card:not(.is-leaving) [data-hotspot]")].map(
      (node) => {
        const box = node.getBoundingClientRect();
        return { id: node.dataset.hotspot, x: box.x, y: box.y, w: box.width, h: box.height };
      },
    );
    tell({ type: "layout", cards: showing().map((node) => node.dataset.id), hotspots });
  }, 0);
}

stack.addEventListener("click", (event) => {
  const link = event.target.closest("[data-url]");
  if (link) tell({ type: "click", action: "open", url: link.dataset.url });
  const button = event.target.closest("[data-service]");
  if (button) tell({ type: "click", action: "sign_in", service: button.dataset.service });
});
window.addEventListener("resize", report);

// --- Drawing a card ---
function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

const DRAW = { weather, agenda, tasks, sources, sign_in: signIn };

function draw(card) {
  const node = el("article", `card card--${card.kind}`);
  node.dataset.id = card.id;
  node.dataset.kind = card.kind;
  if (card.state) node.dataset.state = card.state;
  const head = el("header", "card__head");
  head.append(el("h2", "card__title", card.title));
  if (card.side) head.append(el("p", "card__side", card.side));
  node.append(head, ...DRAW[card.kind](card));
  return node;
}

function weather(card) {
  const now = el("div", "wx");
  const words = el("div");
  words.append(el("p", "wx__cond", card.conditions), el("p", "quiet", card.feels));
  now.append(el("p", "wx__temp", card.temp), words);
  const range = el("div", "range");
  range.setAttribute("aria-label", `Today from ${card.low} to ${card.high}; now ${card.temp}`);
  range.style.setProperty("--at", card.at);
  const track = el("span", "range__track");
  track.append(el("i"));
  range.append(el("span", "range__low", card.low), track, el("span", "range__high", card.high));
  const facts = el("dl", "facts");
  facts.append(
    fact("Rain", card.rain, card.rain_likely ? "amber" : ""),
    fact("Wind", card.wind),
    fact("Today", card.later),
  );
  return [now, range, facts];
}

function fact(label, value, className = "") {
  const pair = el("div");
  pair.append(el("dt", "", label), el("dd", className, value));
  return pair;
}

function agenda(card) {
  if (!card.days.length) return [el("p", "empty", card.empty)];
  const list = el("ol", "agenda");
  for (const day of card.days) {
    if (day.label) list.append(el("li", "agenda__day", day.label));
    day.events.forEach((event, i) => {
      const row = el("li", "event");
      row.classList.toggle("is-past", event.past);
      row.classList.toggle("is-next", event.next);
      row.classList.toggle("is-first", i === 0); // the day's line starts and ends at its dots
      row.classList.toggle("is-last", i === day.events.length - 1);
      const what = el("div");
      what.append(el("p", "event__title", event.title));
      if (event.where) what.append(el("p", "event__where", event.where));
      row.append(el("time", "", event.time), el("span", "event__dot"), what);
      list.append(row);
    });
  }
  return card.more ? [list, el("p", "more", `${card.more} more`)] : [list];
}

function tasks(card) {
  if (!card.tasks.length) return [el("p", "empty", card.empty)];
  const list = el("ul", "todo");
  for (const task of card.tasks) {
    const row = el("li", "task");
    row.classList.toggle("is-done", task.done);
    const due = el("span", task.overdue ? "task__due amber" : "task__due", task.due ?? "");
    row.append(el("span", "task__box"), el("p", "", task.title), due);
    list.append(row);
  }
  return card.more ? [list, el("p", "more", `${card.more} more`)] : [list];
}

function sources(card) {
  const list = el("ul", "links");
  card.links.forEach((link, i) => {
    const button = el("button", "link");
    button.type = "button";
    button.dataset.url = link.url;
    button.dataset.hotspot = `${card.id}:${i}`;
    button.setAttribute("aria-label", `${link.title}, ${link.site}: opens in your browser`);
    button.append(el("span", "link__title", link.title), el("span", "link__site", link.site), outward());
    const item = el("li");
    item.append(button);
    list.append(item);
  });
  return [list];
}

function outward() {
  // A small arrow out of the corner: the page opens in your browser.
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", "M4.5 2.5h5v5M9.5 2.5l-7 7");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.4");
  path.setAttribute("stroke-linecap", "round");
  svg.append(path);
  return svg;
}

function signIn(card) {
  if (!card.button) {
    // Waiting for you in the browser, or signed in.
    const state = el("p", "state");
    state.append(el("i"), el("span", "", card.text));
    return [state];
  }
  const button = el("button", "act", card.button);
  button.type = "button";
  button.dataset.service = card.service;
  button.dataset.hotspot = card.id;
  return [el("p", "sign-in__text", card.text), button];
}

// --- Cards coming and going ---
const showing = () => [...stack.querySelectorAll(".card:not(.is-leaving)")]; // oldest first
const find = (id) => showing().find((node) => node.dataset.id === id);
const waitsForYou = (node) => node.dataset.kind === "sign_in" && WAITING.has(node.dataset.state);

function put(card) {
  if (!DRAW[card.kind]) return;
  const node = draw(card);
  const old = find(card.id);
  if (old) {
    old.replaceWith(node); // same place in the stack, new content
    node.classList.add("is-renewed");
  } else {
    stack.append(node);
    node.classList.add("is-arriving");
  }
  setTimeout(() => {
    node.classList.remove("is-arriving", "is-renewed");
    report();
  }, SETTLE_MS);
  keepTime(node);
  makeRoom();
  if (!awake) fadeLater(); // it came after the conversation ended: it goes like the rest
  report();
}

function keepTime(node) {
  // Sign-in cards keep their own time: they wait for you, not for the conversation.
  const id = node.dataset.id;
  clearTimeout(timers.get(id));
  timers.delete(id);
  if (node.dataset.kind !== "sign_in") return;
  const after = node.dataset.state === "signed_in" ? DONE_MS : WAITING_MS;
  timers.set(id, setTimeout(() => leave(find(id)), after));
}

function leave(node) {
  if (!node || node.classList.contains("is-leaving")) return;
  clearTimeout(timers.get(node.dataset.id));
  timers.delete(node.dataset.id);
  node.style.height = `${node.offsetHeight}px`; // a fixed height, so it can shrink away
  node.getBoundingClientRect(); // (applies it before the transition starts)
  node.classList.add("is-leaving");
  Object.assign(node.style, { height: "0px", paddingTop: "0px", paddingBottom: "0px", borderWidth: "0px" });
  setTimeout(() => {
    node.remove();
    report();
  }, 700);
  report(); // its links stop counting at once
}

function makeRoom() {
  // Too many cards for the panel: the oldest go first (never one waiting for you).
  const padding = getComputedStyle(stack);
  const room = stack.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom);
  const gap = parseFloat(padding.rowGap) || 0;
  const cards = showing();
  let needed = cards.reduce((sum, node) => sum + node.offsetHeight + gap, -gap);
  for (const node of cards.slice(0, -1)) {
    if (needed <= room) break;
    if (waitsForYou(node)) continue;
    needed -= node.offsetHeight + gap;
    leave(node);
  }
}

function fadeLater(after = FADE_AFTER_MS) {
  clearTimeout(fadeTimer);
  fadeTimer = setTimeout(fade, after);
}

function fade() {
  if (hovered) return fadeLater(HOVER_GRACE_MS); // you're about to click one
  for (const node of showing()) if (!waitsForYou(node)) leave(node);
}

window.cards = {
  put,
  update({ awake: listening, state } = {}) {
    if (listening !== undefined) {
      awake = Boolean(listening);
      if (awake) clearTimeout(fadeTimer);
      else fadeLater();
    }
    if (state === "away") {
      // He has gone: there's nobody to sign in with any more.
      for (const node of showing()) if (node.dataset.kind === "sign_in") leave(node);
    }
  },
  clear() {
    for (const node of showing()) leave(node);
  },
  place({ side, align } = {}) {
    document.body.classList.toggle("orb-left", side === "left");
    document.body.classList.toggle("orb-right", side !== "left");
    document.body.classList.toggle("align-top", align === "top");
    document.body.classList.toggle("align-bottom", align !== "top");
    report();
  },
  hover(id) {
    hovered = id || null;
    for (const node of stack.querySelectorAll(".is-hover")) node.classList.remove("is-hover");
    if (hovered) stack.querySelector(`[data-hotspot="${CSS.escape(hovered)}"]`)?.classList.add("is-hover");
  },
};

// --- Demo mode: cards.html#demo (#demo&still: every card at once, no motion, for screenshots) ---
const SAMPLES = {
  weather: {
    id: "weather", kind: "weather", title: "Vancouver", temp: "14°", conditions: "Overcast",
    feels: "Feels like 12°", low: "9°", high: "16°", at: 0.71, rain: "80%", rain_likely: true,
    wind: "18 km/h", later: "Light rain",
  },
  agenda: {
    id: "agenda", kind: "agenda", title: "Today", side: "Tue 6 Oct", more: 1, empty: "Nothing on.",
    days: [{ label: null, events: [
      { time: "9:30 AM", title: "Standup", where: null, past: true, next: false },
      { time: "12 PM", title: "Lunch with Sam", where: "Main Café", past: false, next: true },
      { time: "3:30 PM", title: "Dentist", where: "Main Dental, 1200 W Main Ave", past: false, next: false },
    ] }],
  },
  week: {
    id: "agenda", kind: "agenda", title: "6–11 Oct", side: null, more: 0, empty: "Nothing on.",
    days: [
      { label: "Today", events: [
        { time: "12 PM", title: "Lunch with Sam", where: "Main Café", past: false, next: true },
      ] },
      { label: "Thursday", events: [
        { time: "All day", title: "Sam's birthday", where: null, past: false, next: false },
        { time: "7 PM", title: "Dinner at Jamie's parents'", where: null, past: false, next: false },
      ] },
    ],
  },
  tasks: {
    id: "tasks", kind: "tasks", title: "To-dos", side: null, more: 0, empty: "Nothing due.",
    tasks: [
      { title: "Call the bank about the mortgage renewal", due: "Overdue", overdue: true, done: false },
      { title: "Buy milk", due: "Tomorrow", overdue: false, done: false },
      { title: "File taxes", due: "Thu 1 Oct", overdue: false, done: true },
    ],
  },
  sources: {
    id: "sources", kind: "sources", title: "Further reading",
    links: [
      { title: "Releases · langchain-ai/langgraph", site: "github.com", url: "https://github.com/langchain-ai/langgraph/releases" },
      { title: "langgraph · PyPI", site: "pypi.org", url: "https://pypi.org/project/langgraph/" },
      { title: "What's new in LangGraph v1 - Docs by LangChain", site: "docs.langchain.com", url: "https://docs.langchain.com/oss/python/releases/langgraph-v1" },
    ],
  },
};
const SIGN_IN_STEPS = [
  { state: "sign_in_needed", text: "Your calendar and to-dos need a Google sign-in.", button: "Sign in with Google" },
  { state: "signing_in", text: "Finish signing in in your browser.", button: null },
  { state: "signed_in", text: "Signed in.", button: null },
];

function demo() {
  const body = document.body;
  body.classList.add("demo");
  const form = document.querySelector(".controls");
  form.addEventListener("change", () => {
    const data = new FormData(form);
    body.classList.toggle("light", data.get("backdrop") === "light");
    body.classList.toggle("busy", data.get("backdrop") === "busy");
    const left = data.get("side") === "left";
    window.cards.place({ side: left ? "left" : "right", align: left ? "top" : "bottom" });
    window.cards.update({ awake: data.has("awake") });
  });
  form.addEventListener("click", (event) => {
    const which = event.target.dataset?.demo;
    if (!which) return;
    if (which === "clear") return window.cards.clear();
    if (which !== "sign_in") return put(SAMPLES[which]);
    SIGN_IN_STEPS.forEach((step, i) =>
      setTimeout(() => put({ id: "sign_in:google", kind: "sign_in", service: "google", title: "Google", ...step }), i * 2500));
  });
  window.cards.update({ awake: true });
  if (location.hash.includes("still")) {
    body.classList.add("still");
    body.classList.toggle("light", location.hash.includes("light"));
    body.classList.toggle("busy", location.hash.includes("busy"));
    if (location.hash.includes("left")) window.cards.place({ side: "left", align: "top" });
    for (const name of ["weather", "agenda", "sources"]) put(SAMPLES[name]);
    put({ id: "sign_in:google", kind: "sign_in", service: "google", title: "Google", ...SIGN_IN_STEPS[0] });
  }
}
if (location.hash.startsWith("#demo")) demo();
