let csrfToken = "";
let controlState = { connected: false, hydrated: false };
let portfolioState = { labs: [], edges: [], findings: [], observations: [], eventNetwork: null };
let isConnecting = false;
let runtimeAction = "start";
let renderedRoute = "";
// Which lab this browser is looking at. Selection is per viewer, never a
// server-side switch, so many browsers can inspect different labs at once.
let selectedLabId = null;

function readStored(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value == null ? fallback : JSON.parse(value);
  } catch (error) {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    /* per-viewer convenience only */
  }
}

let openTabs = Array.isArray(readStored("efferents-open-labs", []))
  ? readStored("efferents-open-labs", [])
  : [];
const storedSelected = readStored("efferents-selected-lab", null);
if (typeof storedSelected === "string" && storedSelected) selectedLabId = storedSelected;

function labPath(kind) {
  return `/api/labs/${encodeURIComponent(selectedLabId)}/${kind}`;
}
let labBudget = { spent: 0, cap: 0 };
let portfolioBudget = { spent: 0, cap: 0 };

function renderBudget() {
  const route = currentRoute();
  const meta = document.getElementById("budget-meta");
  const isNetwork = route === "network";
  const budget = isNetwork ? portfolioBudget : labBudget;
  const show = isNetwork
    ? portfolioState.labs.length > 0
    : route === "observe" && controlState.connected;
  meta.hidden = !show;
  if (!show) return;
  const percent = budget.cap > 0
    ? Math.min(100, Math.max(0, (budget.spent / budget.cap) * 100))
    : 0;
  text(
    "budget",
    `${isNetwork ? "all labs" : "this lab"} · ` +
      `$${budget.spent.toFixed(2)} / $${budget.cap.toFixed(2)} daily`,
  );
  document.getElementById("budget-fill").style.width = `${percent}%`;
}

async function getJSON(path) {
  const response = await fetch(path);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `${path} returned ${response.status}`);
  }
  return payload;
}

async function postJSON(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Efferents-CSRF": csrfToken,
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `${path} returned ${response.status}`);
  }
  return body;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function text(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value == null ? "" : String(value);
}

function showMessage(id, message = "", type = "") {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = message;
  element.className = `form-message${type ? ` ${type}` : ""}`;
}

function formatMetric(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const magnitude = Math.abs(number);
  if (magnitude !== 0 && (magnitude >= 10000 || magnitude < 0.0001)) {
    return number.toExponential(3);
  }
  return number.toLocaleString(undefined, { maximumSignificantDigits: 6 });
}

function quantile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const position = (sorted.length - 1) * fraction;
  const lower = Math.floor(position);
  const remainder = position - lower;
  return sorted[lower + 1] == null
    ? sorted[lower]
    : sorted[lower] + remainder * (sorted[lower + 1] - sorted[lower]);
}

function compactRunId(value) {
  const runId = String(value || "");
  if (runId.length <= 22) return runId || "—";
  return `…${runId.slice(-21)}`;
}

function formatTimestamp(value, compact = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value).replace("T", " ").slice(0, compact ? 16 : 19);
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: compact ? undefined : "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}` +
    (compact ? "" : `:${get("second")}`);
}

function formatRelativeTime(value) {
  if (!value) return "no activity";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return formatTimestamp(value, true);
  const elapsed = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function currentRoute() {
  const route = window.location.hash.replace(/^#/, "");
  if (route === "steer") return "observe";
  return ["connect", "observe", "network"].includes(route) ? route : "connect";
}

function renderRoute() {
  const networkWasHidden = document.getElementById("network-view").hidden;
  let route = currentRoute();
  if (controlState.hydrated && !controlState.connected && !["connect", "network"].includes(route)) {
    route = "connect";
  }
  if (window.location.hash !== `#${route}`) {
    history.replaceState(null, "", `#${route}`);
  }
  document.querySelectorAll("[data-route-view]").forEach((view) => {
    view.hidden = view.dataset.routeView !== route;
  });
  document.querySelectorAll("[data-route-link]").forEach((link) => {
    if (link.dataset.routeLink === route) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
  applyLabRail(route);
  if (renderedRoute && renderedRoute !== route) window.scrollTo(0, 0);
  renderedRoute = route;
  document.title = `efferents — ${route}`;
  renderLabTabs();
  renderBudget();
  if (route === "network" && networkWasHidden) renderNetwork();
}

// The labs rail pops in and out from the topbar. Collapsed by default so the
// network gets the full width; the choice is a per-browser convenience.
let labRailOpen = readStored("efferents-lab-rail", false) === true;

function applyLabRail(route) {
  const available = route !== "connect" && portfolioState.labs.length > 0;
  const toggle = document.getElementById("lab-rail-toggle");
  const showRail = available && labRailOpen;
  toggle.hidden = !available;
  toggle.setAttribute("aria-expanded", String(showRail));
  text("lab-rail-toggle-count", String(portfolioState.labs.length).padStart(2, "0"));
  document.getElementById("lab-rail").hidden = !showRail;
  document.getElementById("workspace-frame").classList.toggle("with-lab-rail", showRail);
}

function initLabRailToggle() {
  document.getElementById("lab-rail-toggle").addEventListener("click", () => {
    labRailOpen = !labRailOpen;
    writeStored("efferents-lab-rail", labRailOpen);
    applyLabRail(currentRoute());
    if (currentRoute() === "network") renderNetwork();
  });
}

function initRouting() {
  window.addEventListener("hashchange", renderRoute);
  document.querySelectorAll("[data-route-link]").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (link.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        showMessage("connect-message", "Connect a lab before opening this workspace.", "error");
      }
    });
  });
  renderRoute();
}

function setRuntimeStatus(status) {
  const normalized = String(status || "stopped").toLowerCase();
  const badge = document.getElementById("status-badge");
  badge.className = `status-badge ${normalized}`;
  text("status-text", normalized);
  text(
    "steer-runtime-state",
    normalized === "running"
      ? "agent is running"
      : normalized === "paused"
        ? "paused snapshot"
        : "queued locally",
  );
}

function setContractState(contract, phase = "result") {
  document.querySelectorAll("[data-contract]").forEach((item) => {
    const key = item.dataset.contract;
    const state = item.querySelector(".check-state");
    item.classList.remove("checking", "passed");
    if (phase === "checking") {
      item.classList.add("checking");
      state.textContent = "checking";
    } else if (contract?.[key]) {
      item.classList.add("passed");
      state.textContent = "passed";
    } else {
      state.textContent = "waiting";
    }
  });
}

function renderSteering(records) {
  const steering = Array.isArray(records) ? records : [];
  text("steering-count", `${steering.length} ${steering.length === 1 ? "record" : "records"}`);
  const element = document.getElementById("steering-history");
  if (!steering.length) {
    element.innerHTML = '<div class="empty-state">No directions</div>';
    return;
  }
  element.innerHTML = steering.map((record) => {
    const mode = String(record.mode || "auto").replace(/_/g, " ");
    const label = record.action ? String(record.action) : mode;
    const who = record.by ? ` · ${esc(record.by)}` : "";
    const ack = record.acknowledged === false ? " · queued" : "";
    return `<article class="steering-record">` +
      `<div class="steering-record-meta">` +
        `<time>${esc(formatTimestamp(record.timestamp, true))} UTC</time>` +
        `<span class="steering-mode">${esc(label)}${who}${ack}</span>` +
      `</div>` +
      `<p>${esc(record.message || "")}</p>` +
    `</article>`;
  }).join("");
}

function renderControl(info) {
  if (info.csrf_token) csrfToken = info.csrf_token;
  controlState = { ...controlState, ...info };
  const connected = Boolean(info.connected);
  const pausedDemo = Boolean(info.paused_demo);
  controlState.connected = connected;
  controlState.hydrated = true;

  const connectionBar = document.getElementById("connection-bar");
  connectionBar.hidden = !connected;
  renderBudget();

  if (!connected) {
    setRuntimeStatus("disconnected");
    if (!isConnecting) setContractState(info.contract);
    renderSteering([]);
    renderRoute();
    return;
  }

  text("observe-lab-title", labDisplayName(info.lab_id));
  text("observe-lab-meta", `${info.domain || "unclassified"} / ${info.status || "stopped"}`);
  text("connection-source", info.source || info.submission_dir || "local submission");
  setRuntimeStatus(info.status);
  document.getElementById("trial-lab").disabled = pausedDemo || info.status === "running";
  setContractState(info.contract);
  renderSteering(info.steering);

  const keyState = document.getElementById("api-key-state");
  keyState.textContent = pausedDemo
    ? "Paused demo · no model calls"
    : info.has_api_key ? "API key ready" : "API key missing";
  keyState.classList.toggle("ready", Boolean(info.has_api_key) && !pausedDemo);
  document.getElementById("activity-state").innerHTML = pausedDemo
    ? '<span aria-hidden="true"></span> history'
    : '<span aria-hidden="true"></span> live';
  const live = info.status === "running" || info.status === "paused";
  const ownerPaused = Boolean(info.owner_paused);
  document.getElementById("start-lab").hidden = pausedDemo || live;
  document.getElementById("stop-lab").hidden = pausedDemo || !live;
  document.getElementById("pause-lab").hidden = pausedDemo || !live || ownerPaused;
  document.getElementById("resume-lab").hidden = pausedDemo || !ownerPaused;
  document.getElementById("connect-submit").disabled = pausedDemo;
  const steerForm = document.getElementById("steer-form");
  steerForm.querySelectorAll("textarea, select, button").forEach((control) => {
    control.disabled = pausedDemo;
  });
  const activityPanel = document.querySelector(".activity-panel");
  if (pausedDemo && !activityPanel.dataset.pausedDefault) {
    activityPanel.dataset.pausedDefault = "collapsed";
    activityPanel.classList.add("collapsed");
    const activityToggle = activityPanel.querySelector("[data-panel-toggle]");
    activityToggle.setAttribute("aria-expanded", "false");
    activityToggle.textContent = "Show";
  }
  renderRoute();
}

function selectedPortfolioLab() {
  if (selectedLabId) {
    return portfolioState.labs.find((lab) => lab.lab_id === selectedLabId) || null;
  }
  return portfolioState.labs.find((lab) => lab.selected) || null;
}

function markSelected() {
  const chosen = selectedPortfolioLab();
  portfolioState.labs.forEach((lab) => {
    lab.selected = Boolean(chosen) && lab.lab_id === chosen.lab_id;
  });
}

function renderLabTabs() {
  const strip = document.getElementById("lab-tabs");
  const route = currentRoute();
  const known = new Map(portfolioState.labs.map((lab) => [lab.lab_id, lab]));
  openTabs = openTabs.filter((labId) => known.has(labId));
  const selected = selectedPortfolioLab();
  if (selected && route === "observe" && !openTabs.includes(selected.lab_id)) {
    openTabs.push(selected.lab_id);
  }
  writeStored("efferents-open-labs", openTabs);
  strip.hidden = route === "connect" || portfolioState.labs.length === 0;
  const networkActive = route === "network";
  strip.innerHTML =
    `<button class="lab-tab home${networkActive ? " active" : ""}" type="button" ` +
    `data-tab-network aria-current="${networkActive ? "true" : "false"}">` +
    `<span class="tab-name">network</span></button>` +
    openTabs.map((labId) => {
      const lab = known.get(labId);
      const active = Boolean(lab.selected) && route === "observe";
      return `<button class="lab-tab${active ? " active" : ""}" type="button" ` +
        `data-tab="${esc(labId)}" aria-current="${active ? "true" : "false"}">` +
        `<i class="tab-led ${esc(lab.status || "stopped")}" aria-hidden="true"></i>` +
        `<span class="tab-name">${esc(labDisplayName(labId))}</span>` +
        `<span class="tab-close" data-close="${esc(labId)}" title="Close tab">×</span></button>`;
    }).join("");
  strip.querySelector("[data-tab-network]").addEventListener("click", () => {
    window.location.hash = "network";
  });
  strip.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.addEventListener("click", async (event) => {
      const close = event.target.closest("[data-close]");
      if (close) {
        event.stopPropagation();
        await closeLabTab(close.dataset.close);
        return;
      }
      if (tab.dataset.tab === selectedPortfolioLab()?.lab_id) {
        window.location.hash = "observe";
      } else {
        await selectPortfolioLab(tab.dataset.tab, true);
      }
    });
  });
}

async function closeLabTab(labId) {
  const wasActive = selectedPortfolioLab()?.lab_id === labId;
  openTabs = openTabs.filter((openId) => openId !== labId);
  writeStored("efferents-open-labs", openTabs);
  if (wasActive && openTabs.length) {
    await selectPortfolioLab(openTabs[openTabs.length - 1], true);
    return;
  }
  if (wasActive) {
    window.location.hash = "network";
  }
  renderLabTabs();
}

async function openLabTab(labId) {
  if (!openTabs.includes(labId)) openTabs.push(labId);
  writeStored("efferents-open-labs", openTabs);
  await selectPortfolioLab(labId, true);
}

// Ideas (autoresearchers) belong to a lab and are shown by name. A verdict
// falsifies an idea, never the lab that hosts it.
const IDEA_NAME_STOPWORDS = new Set("a an and at by for from in of on or that the to with".split(" "));
function ideaName(idea) {
  if (idea.name) return idea.name;
  if (idea.id && idea.id !== "primary") return idea.id.replace(/[-_]/g, " ");
  const words = String(idea.focus || "").split(/\s+/).filter(Boolean).slice(0, 6);
  while (words.length && IDEA_NAME_STOPWORDS.has(words.at(-1).toLowerCase())) words.pop();
  return words.join(" ").replace(/[ .,:;?!]+$/, "") || idea.id || "idea";
}

function labIdeas(lab) {
  if (lab.ideas?.length) return lab.ideas;
  const focus = lab.hypothesis?.question || lab.approach || "Initial research idea";
  return [{id: "primary", focus, verdict: lab.verdict?.status || "undecided"}];
}

function ideaLineMarkup(idea) {
  const falsified = idea.verdict === "falsified";
  return `<span class="lab-idea-node${falsified ? " falsified" : ""}" title="${esc(idea.focus || "")}">` +
    `${esc(ideaName(idea))}${falsified ? " · falsified" : ""}</span>`;
}

function renderLabRail() {
  const labs = Array.isArray(portfolioState.labs) ? portfolioState.labs : [];
  text("lab-portfolio-count", String(labs.length).padStart(2, "0"));
  const list = document.getElementById("lab-list");
  if (!labs.length) {
    list.innerHTML = '<div class="empty-state">No local labs</div>';
    renderRoute();
    return;
  }
  list.innerHTML = labs.map((lab, index) => {
    const headline = lab.headline || {};
    const metric = headline.best == null
      ? `${headline.observations || 0} observations`
      : `${esc(headline.column || "metric")} ${esc(formatMetric(headline.best))}`;
    const ownerLine = lab.owner_name ? ` · ${esc(lab.owner_name)}` : "";
    return `<button class="lab-list-item${lab.selected ? " selected" : ""}" ` +
      `type="button" data-lab-select="${esc(lab.lab_id)}" role="listitem" ` +
      `aria-current="${lab.selected ? "true" : "false"}">` +
      `<span class="lab-seq">${String(index + 1).padStart(2, "0")}</span>` +
      `<span class="lab-list-copy"><strong>${esc(labDisplayName(lab.lab_id))}</strong>` +
      `<small>${esc(lab.domain || "unclassified")}${ownerLine}</small>` +
      `<span>${metric}</span>` +
      `<span class="lab-ideas">${labIdeas(lab).map(ideaLineMarkup).join("")}</span></span>` +
      `<span class="lab-list-state ${esc(lab.status || "stopped")}">` +
      `<i aria-hidden="true"></i>${esc(formatRelativeTime(lab.last_activity))}</span>` +
      `</button>`;
  }).join("");
  list.querySelectorAll("[data-lab-select]").forEach((button) => {
    button.addEventListener("click", async () => {
      await openLabTab(button.dataset.labSelect);
    });
  });
  renderRoute();
}

function portfolioNodePosition(index, count) {
  const angle = (-Math.PI / 2) + ((Math.PI * 2 * index) / Math.max(count, 1));
  return {
    x: 50 + Math.cos(angle) * (count < 3 ? 29 : 35),
    y: 50 + Math.sin(angle) * (count < 3 ? 27 : 34),
  };
}

// Geometry adapted from docs/prototypes/event-network.html: journal hubs,
// curved home branches, lab nuclei, idea tips and a selected-lab inspector.
let networkSelection = null;
function homeJournal(lab) {
  if (lab.journal) return lab.journal;
  const domain = (lab.domain || "").toLowerCase();
  if (/machine.learning|active.learning|^ml$/.test(domain)) return "ML & Autonomous Systems";
  if (/vehicle|traffic|simulation|routing|transport/.test(domain)) return "Simulation & Autonomous Systems";
  if (/physics|orbit|mechanics/.test(domain)) return "Physics & Dynamics";
  if (/math|graph|numerical|algorithm|optimi/.test(domain)) return "Mathematics & Computation";
  return lab.domain || "General Research";
}
function portfolioLabs() {
  const localIds = new Set(portfolioState.labs.map(lab => lab.lab_id));
  return [...portfolioState.labs,
    ...(portfolioState.eventNetwork?.labs || []).filter(lab => !localIds.has(lab.lab_id))];
}

function labDisplayName(labId) {
  if (!labId) return "Unnamed lab";
  const labs = portfolioLabs();
  if (!labs.some(lab => lab.lab_id === labId)) labs.push({lab_id: labId});
  return networkLabNames(labs).get(labId);
}

function networkLabNames(labs) {
  const names = new Map();
  const counts = new Map();
  let placeholder = 0;
  const placeholderName = () => {
    let index = ++placeholder;
    let suffix = "";
    while (index > 0) {
      suffix = String.fromCharCode(65 + (index - 1) % 26) + suffix;
      index = Math.floor((index - 1) / 26);
    }
    return `Lab ${suffix}`;
  };
  [...labs].sort((a, b) => a.lab_id.localeCompare(b.lab_id)).forEach(lab => {
    const actualName = String(lab.display_name || "").trim();
    const generatedId = /-[a-f0-9]{8,}$/i.test(lab.lab_id || "");
    const base = actualName || (generatedId || !lab.lab_id
      ? placeholderName()
      : lab.lab_id.replace(/[-_]+/g, " ").replace(/^./, c => c.toUpperCase()));
    const count = (counts.get(base) || 0) + 1;
    counts.set(base, count);
    names.set(lab.lab_id, {base, count});
  });
  return new Map([...names].map(([id, {base, count}]) =>
    [id, counts.get(base) > 1 ? `${base} ${count}` : base]));
}
function publishedFindings() {
  return [...portfolioState.findings, ...(portfolioState.eventNetwork?.findings || [])]
    .filter(item => item.kind === "publication" && item.publication_status === "accepted");
}

function reviewBoardMarkup(board = {}) {
  const personas = ["critical", "neutral", "optimistic"];
  const scores = board.scores || {};
  return `<div class="review-board-title">Review board <small>${esc(board.status || "awaiting paper")}</small></div>` +
    `<div class="review-scores">${personas.map(persona => {
      const score = scores[persona] ?? (persona === "optimistic" ? scores.enthusiast : null);
      return `<div><span>${persona}</span><strong>${Number.isInteger(score) ? `${score}/10` : "—"}</strong></div>`;
    }).join("")}</div>`;
}

function renderNetwork() {
  if (document.getElementById("network-view").hidden) return;
  const labs = portfolioLabs();
  const findings = publishedFindings();
  const publicationById = new Map(findings.map(item => [item.id, item]));
  const observations = [...portfolioState.observations, ...(portfolioState.eventNetwork?.observations || [])]
    .filter(item => publicationById.has(item.finding_id));
  const lines = document.getElementById("network-lines");
  const nodes = document.getElementById("network-nodes");
  const journalsLayer = document.getElementById("network-journals");
  const ideasLayer = document.getElementById("network-ideas");
  [lines, nodes, journalsLayer, ideasLayer].forEach(el => el.replaceChildren());
  document.querySelector(".network-hub").hidden = true;
  const empty = document.getElementById("network-empty");
  empty.hidden = labs.length > 0;
  empty.textContent = "Connect a lab to build a reviewed journal.";
  const selected = labs.find(lab => lab.lab_id === networkSelection) || labs[0];
  networkSelection = selected?.lab_id || null;
  const groups = new Map();
  labs.forEach(lab => groups.set(homeJournal(lab), [...(groups.get(homeJournal(lab)) || []), lab]));
  const names = [...groups.keys()];
  const sizes = names.map(name => groups.get(name).length);
  // Re-layout when the lab set, the journals, or the viewport changed; never on a plain poll.
  const change = sizeMapViewport([...labs.map(lab => lab.lab_id).sort(), ...names].join("|"));
  if (change.content || change.view) mapView.layout = chooseMapLayout(sizes);
  const {cols: columns, groupCols} = mapView.layout;
  const world = layoutMapGroups(sizes, columns, groupCols);
  const rowHeight = MAP_CELL.row;
  lines.setAttribute("viewBox", `0 0 ${world.width} ${world.height}`);
  const defs = svgElement("defs", {});
  for (const kind of ["publish", "subscribe", "accepted", "rejected"]) {
    const marker = svgElement("marker", {id: `${kind}-arrow`, viewBox: "0 0 10 10",
      refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto"});
    marker.appendChild(svgElement("path", {d: "M0 0 L10 5 L0 10 Z", class: `${kind}-arrow`}));
    defs.appendChild(marker);
  }
  lines.appendChild(defs);
  const route = (d, kind, active, label) => {
    const edge = svgElement("path", {d, fill: "none", class: `journal-route ${kind}${active ? " active" : " pending"}`,
      "marker-end": `url(#${kind}-arrow)`});
    const title = svgElement("title", {}); title.textContent = label;
    edge.appendChild(title); lines.appendChild(edge);
    if (active) {
      const dot = svgElement("circle", {r: 3, class: `network-packet ${kind}`});
      dot.appendChild(svgElement("animateMotion", {dur: "4s", repeatCount: "indefinite", path: d}));
      lines.appendChild(dot);
    }
  };
  const labPorts = new Map(), journalPorts = new Map();
  names.forEach((name, groupIndex) => {
    const members = groups.get(name);
    const {x: groupLeft, y: groupTop, width: groupWidth} = world.origins[groupIndex];
    const groupRight = groupLeft + groupWidth;
    const rows = Math.ceil(members.length / columns);
    const journalY = groupTop + rows * rowHeight + 15;
    const width = MAP_CELL.card, half = width / 2;
    members.forEach((lab, index) => {
      const x = groupLeft + MAP_CELL.pad + (index % columns + 0.5) * MAP_CELL.slot;
      const y = groupTop + Math.floor(index / columns) * rowHeight + 24;
      const card = document.createElement("article");
      card.className = "network-lab-boundary";
      Object.assign(card.style, {left: `${x}px`, top: `${y}px`, width: `${width}px`});
      const ideas = labIdeas(lab);
      card.innerHTML = `<button type="button" class="lab-identity" aria-pressed="${lab.lab_id === networkSelection}"><strong>${esc(labDisplayName(lab.lab_id))}</strong><small>${esc(lab.status || "stopped")}${lab.remote ? " · read only" : ""}</small></button>` +
        `<div class="lab-idea-nodes" aria-label="Ideas in ${esc(labDisplayName(lab.lab_id))}"><span class="lab-idea-label">Ideas · ${ideas.length}</span>${ideas.map(ideaLineMarkup).join("")}</div>` +
        `<div class="internal-research"><span>Supervisor · Researcher · Librarian</span><b>Hypothesis → Experiment</b><b>Evidence → Paper</b><span>Executor · Coder · Analyst · Writer</span></div>`;
      // Local labs open in their tab; remote read-only labs have no local evidence to show.
      card.querySelector("button").onclick = () => {
        networkSelection = lab.lab_id;
        if (lab.remote) renderNetwork(); else openLabTab(lab.lab_id);
      };
      nodes.appendChild(card);
      const owned = findings.filter(item => item.lab_id === lab.lab_id);
      const latest = owned.at(-1);
      const board = lab.review_board || (latest ? {status: "accepted", scores: latest.review_scores} : {});
      const review = document.createElement("div");
      review.className = "network-review-board";
      Object.assign(review.style, {left: `${x}px`, top: `${y + 242}px`, width: `${width}px`});
      review.innerHTML = reviewBoardMarkup(board);
      nodes.appendChild(review);
      route(`M${x},${y + 210} V${y + 237}`, "publish", false,
        `${labDisplayName(lab.lab_id)} submits a paper to its three-reviewer board`);
      if (board.status === "rejected") {
        route(`M${x + half},${y + 300} H${x + half + 20} V${y + 110} H${x + half + 5}`,
          "rejected", true, `Rejected paper → ${labDisplayName(lab.lab_id)} for revision`);
      }
      // Each lab publishes down the gutter on its right and subscribes up the
      // gutter on its left, so paths stay out of other labs in larger journals.
      const lane = x + half + 35;
      const accepted = board.status === "accepted" && owned.some(item =>
        !board.campaign_id || item.campaign_id === board.campaign_id);
      if (board.status !== "rejected") {
        const entry = lane > groupRight - 100 ? `V${journalY + 35} H${groupRight - 100}` : `V${journalY}`;
        route(`M${x},${y + 332} V${y + 350} H${lane} ${entry}`,
          accepted ? "accepted" : "publish", accepted, `${labDisplayName(lab.lab_id)} → ${name}: accepted papers only`);
      }
      labPorts.set(lab.lab_id, {x: x - half, y: y + 75, lane: x - half - 35});
    });
    const published = findings.filter(item => (item.journal || homeJournal(item)) === name);
    const journal = document.createElement("div");
    journal.className = "shared-journal-node";
    Object.assign(journal.style, {left: `${groupLeft + 100}px`, top: `${journalY}px`, width: `${groupWidth - 200}px`});
    journal.innerHTML = `<small>SHARED JOURNAL · ACCEPTED PAPERS</small><strong>${esc(name)}</strong><small>${published.length} publications · append-only evidence</small>`;
    journalsLayer.appendChild(journal);
    journalPorts.set(name, {x: groupRight - 150, y: journalY + 70});
    // Subscription capability is visible; animation requires a persisted receipt.
    members.forEach(lab => {
      const port = labPorts.get(lab.lab_id);
      const active = observations.some(item => item.target === lab.lab_id && publicationById.get(item.finding_id)?.journal === name);
      route(`M${groupLeft + 150},${journalY + 70} V${journalY + 94} H${port.lane} V${port.y} H${port.x - 7}`,
        "subscribe", active, `${name} → ${labDisplayName(lab.lab_id)}: journal subscription`);
    });
  });
  const subscriptions = new Set();
  observations.forEach(receipt => {
    const publication = publicationById.get(receipt.finding_id);
    const name = publication.journal || homeJournal(publication);
    const lab = labs.find(item => item.lab_id === receipt.target);
    const journal = journalPorts.get(name), port = labPorts.get(receipt.target);
    const key = `${name}:${receipt.target}`;
    if (!journal || !port || !lab || homeJournal(lab) === name || subscriptions.has(key)) return;
    subscriptions.add(key);
    route(`M${journal.x},${journal.y} V${journal.y + 40} H${port.lane - 10} V${port.y - 16} H${port.x - 7} V${port.y}`,
      "subscribe", true, `${name} → ${labDisplayName(lab.lab_id)}: cross-field journal subscription`);
  });
  setMapWorld(world.width, world.height, change.view || (change.content && !mapView.moved));
  text("network-node-count", `${labs.length} labs · ${groups.size} journals`);
  renderEventAdmin(); renderExchange();
}

// ---------------------------------------------------------- map pan / zoom
// #lab-map is a fixed viewport; #network-world holds every layer at its
// natural layout size and only its transform changes.
const mapView = {x: 0, y: 0, k: 1, width: 1000, height: 460, contentKey: "", viewKey: "", moved: false, layout: {cols: 2, groupCols: 1}};
const MAP_ZOOM = {min: 0.25, max: 2.5, fitMin: 0.35, fitMax: 1, margin: 16, step: 1.3, pan: 60};
// World geometry in px: a lab slot is a card plus its two routing gutters.
const MAP_CELL = {slot: 460, card: 360, pad: 40, row: 410, journal: 150};
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function applyMapView(animated = false) {
  const world = document.getElementById("network-world");
  world.classList.toggle("animated", animated);
  world.style.transform = `translate(${mapView.x}px, ${mapView.y}px) scale(${mapView.k})`;
  text("map-zoom-level", `${Math.round(mapView.k * 100)}%`);
}

function fitMap(animated = false) {
  const map = document.getElementById("lab-map");
  const room = (size) => Math.max(1, size - 2 * MAP_ZOOM.margin);
  mapView.k = clamp(Math.min(room(map.clientWidth) / mapView.width, room(map.clientHeight) / mapView.height),
    MAP_ZOOM.fitMin, MAP_ZOOM.fitMax);
  // Centered; content larger than the viewport at the minimum fit starts at its top-left.
  const center = (view, size) => Math.max(MAP_ZOOM.margin, (view - size * mapView.k) / 2);
  mapView.x = center(map.clientWidth, mapView.width);
  mapView.y = center(map.clientHeight, mapView.height);
  mapView.moved = false;
  applyMapView(animated);
}

function zoomMapAt(px, py, factor, animated = false) {
  const k = clamp(mapView.k * factor, MAP_ZOOM.min, MAP_ZOOM.max);
  mapView.x = px - (px - mapView.x) * k / mapView.k;
  mapView.y = py - (py - mapView.y) * k / mapView.k;
  mapView.k = k;
  mapView.moved = true;
  applyMapView(animated);
}

function zoomMapCentered(factor) {
  const map = document.getElementById("lab-map");
  zoomMapAt(map.clientWidth / 2, map.clientHeight / 2, factor, true);
}

function panMap(dx, dy) {
  mapView.x += dx;
  mapView.y += dy;
  mapView.moved = true;
  applyMapView();
}

// Journal groups flow in rows of groupCols; each group holds its labs in up to
// cols columns and is only as wide as the labs it has.
function layoutMapGroups(sizes, cols, groupCols) {
  const origins = [];
  let top = 0, width = MAP_CELL.slot + 2 * MAP_CELL.pad;
  for (let i = 0; i < sizes.length; i += groupCols) {
    const row = sizes.slice(i, i + groupCols);
    let left = 0;
    row.forEach(size => {
      const groupWidth = Math.min(cols, size) * MAP_CELL.slot + 2 * MAP_CELL.pad;
      origins.push({x: left, y: top, width: groupWidth});
      left += groupWidth;
    });
    width = Math.max(width, left);
    top += Math.max(...row.map(size => Math.ceil(size / cols) * MAP_CELL.row + MAP_CELL.journal));
  }
  return {origins, width, height: Math.max(460, top)};
}

// Pick the column counts whose world shape fits the viewport at the largest scale.
function chooseMapLayout(sizes) {
  const map = document.getElementById("lab-map");
  let best = {cols: 1, groupCols: 1, scale: 0};
  for (let cols = 1; cols <= Math.max(1, ...sizes); cols++) {
    for (let groupCols = 1; groupCols <= Math.max(1, sizes.length); groupCols++) {
      const world = layoutMapGroups(sizes, cols, groupCols);
      const scale = Math.min(map.clientWidth / world.width, map.clientHeight / world.height);
      if (scale > best.scale) best = {cols, groupCols, scale};
    }
  }
  return best;
}

// Size the viewport to the window and report what changed since the last render.
function sizeMapViewport(contentKey) {
  const map = document.getElementById("lab-map");
  const below = map.closest(".panel").getBoundingClientRect().bottom - map.getBoundingClientRect().bottom;
  const top = map.getBoundingClientRect().top + window.scrollY;
  // Fill the window below the map's top; when stacked panels push the map
  // under the fold (narrow screens), give it most of one screen instead.
  const room = Math.floor(window.innerHeight - top - below - MAP_ZOOM.margin);
  map.style.height = `${room >= 320 ? room : Math.floor(window.innerHeight * 0.7)}px`;
  const viewKey = `${map.clientWidth}x${map.clientHeight}`;
  const change = {content: contentKey !== mapView.contentKey, view: viewKey !== mapView.viewKey};
  Object.assign(mapView, {contentKey, viewKey});
  return change;
}

function setMapWorld(width, height, refit) {
  Object.assign(mapView, {width, height});
  Object.assign(document.getElementById("network-world").style, {width: `${width}px`, height: `${height}px`});
  if (refit) fitMap(); else applyMapView();
}

function initMapPanZoom() {
  const map = document.getElementById("lab-map");
  const pointers = new Map();
  let dragging = false;
  map.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = map.getBoundingClientRect();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
    zoomMapAt(event.clientX - rect.left, event.clientY - rect.top, Math.exp(-delta * (event.ctrlKey ? 0.01 : 0.0015)));
  }, {passive: false});
  map.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, a, summary, input, .map-controls")) return;
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    dragging = pointers.size > 1;
  });
  map.addEventListener("pointermove", (event) => {
    const last = pointers.get(event.pointerId);
    if (!last) return;
    const now = {x: event.clientX, y: event.clientY};
    // Movement under 4px stays a click.
    if (!dragging && Math.hypot(now.x - last.x, now.y - last.y) < 4) return;
    if (!dragging) pointers.forEach((_, id) => map.setPointerCapture(id));
    dragging = true;
    map.classList.add("panning");
    const other = [...pointers].find(([id]) => id !== event.pointerId)?.[1];
    if (other) {
      const rect = map.getBoundingClientRect();
      const spread = (point) => Math.hypot(point.x - other.x, point.y - other.y) || 1;
      zoomMapAt((now.x + other.x) / 2 - rect.left, (now.y + other.y) / 2 - rect.top, spread(now) / spread(last));
    }
    panMap((now.x - last.x) / pointers.size, (now.y - last.y) / pointers.size);
    pointers.set(event.pointerId, now);
  });
  const release = (event) => {
    pointers.delete(event.pointerId);
    if (pointers.size) return;
    dragging = false;
    map.classList.remove("panning");
  };
  map.addEventListener("pointerup", release);
  map.addEventListener("pointercancel", release);
  map.addEventListener("keydown", (event) => {
    if (event.target.closest("input") || event.metaKey || event.ctrlKey || event.altKey) return;
    const actions = {
      "+": () => zoomMapCentered(MAP_ZOOM.step), "=": () => zoomMapCentered(MAP_ZOOM.step),
      "-": () => zoomMapCentered(1 / MAP_ZOOM.step), "0": () => fitMap(true),
      ArrowLeft: () => panMap(MAP_ZOOM.pan, 0), ArrowRight: () => panMap(-MAP_ZOOM.pan, 0),
      ArrowUp: () => panMap(0, MAP_ZOOM.pan), ArrowDown: () => panMap(0, -MAP_ZOOM.pan),
    };
    if (!actions[event.key]) return;
    event.preventDefault();
    actions[event.key]();
  });
  map.querySelector(".map-controls").addEventListener("click", (event) => {
    const action = event.target.closest("[data-map-zoom]")?.dataset.mapZoom;
    if (action === "fit") fitMap(true);
    else if (action) zoomMapCentered(action === "in" ? MAP_ZOOM.step : 1 / MAP_ZOOM.step);
  });
  window.addEventListener("resize", renderNetwork);
}

function renderExchange() {
  const findings = new Map(publishedFindings().map(item => [item.id, item]));
  const receipts = [...portfolioState.observations, ...(portfolioState.eventNetwork?.observations || [])].filter(item => findings.has(item.finding_id));
  const feed = document.getElementById("exchange-feed");
  const rows = Array.from(findings.values()).reverse().slice(0, 30);
  feed.innerHTML = rows.length ? rows.map((item) => {
    const observers = [...new Set(receipts.filter((r) => r.finding_id === item.id).map((r) => r.target))];
    return `<article class="exchange-record"><div class="exchange-record-meta"><strong>${esc(labDisplayName(item.lab_id))}</strong>` +
      `<span>${esc(item.kind)} → ${esc(homeJournal(item))}</span></div>` +
      (item.kind === "hypothesis" ? `<details><summary>Experiment claim</summary><p>${esc(item.body)}</p></details>` : `<p>${esc(item.body)}</p>`) + `<div class="exchange-provenance">` +
      `${item.run_id ? `Run ${esc(item.run_id)} · ` : ""}Record ${esc(item.id.slice(0, 12))}` +
      `</div><div class="exchange-receipt">${observers.length ? `Received by ${observers.map(id => esc(labDisplayName(id))).join(", ")}` : "Awaiting a peer visit"}</div></article>`;
  }).join("") : "";
  // The panel carries no copy of its own; it appears only once papers exist.
  document.getElementById("exchange-panel").hidden = !rows.length;
  const goals = [...new Set(portfolioState.labs.map((lab) => lab.goal).filter(Boolean))];
  document.getElementById("known-goals").innerHTML = goals.map((goal) => `<option value="${esc(goal)}"></option>`).join("");
}

function renderEventAdmin() {
  const network = portfolioState.eventNetwork;
  const panel = document.getElementById("event-admin-panel");
  panel.hidden = !network?.configured;
  if (!network?.configured) return;
  const event = network.event || {};
  text("event-admin-meta", network.available ? "live · sanitized summaries" : "registry unavailable");
  text("event-id", event.event_id || "—");
  text("event-spend", `$${Number(event.spent_usd || 0).toFixed(4)} / $${Number(event.total_cap_usd || 0).toFixed(2)}`);
  text("event-token-count", String(event.token_count ?? network.tokens?.length ?? 0));
  text("event-generated-at", formatTimestamp(network.generated_at, true));
  const body = document.querySelector("#event-tokens tbody");
  const tokens = Array.isArray(network.tokens) ? network.tokens : [];
  body.innerHTML = tokens.length ? tokens.map((token) =>
    `<tr><td>${esc(token.token_id)}</td><td>${esc(labDisplayName(token.lab_id))}</td>` +
    `<td>${esc(token.status)}</td><td>$${Number(token.spent_usd || 0).toFixed(4)} / $${Number(token.cap_usd || 0).toFixed(2)}</td>` +
    `<td>${esc(token.requests || 0)}</td><td>${esc(formatTimestamp(token.last_sync_at, true))}</td></tr>`
  ).join("") : '<tr><td colspan="6" class="empty-state">No event tokens issued</td></tr>';
}

function renderPortfolio(payload) {
  portfolioState = {
    labs: Array.isArray(payload?.labs) ? payload.labs : [],
    edges: Array.isArray(payload?.edges) ? payload.edges : [],
    findings: Array.isArray(payload?.findings) ? payload.findings : [],
    observations: Array.isArray(payload?.observations) ? payload.observations : [],
    eventNetwork: payload?.event_network || null,
  };
  if (!selectedLabId) {
    // First load in a single-lab workspace: follow the server's default lab.
    const serverDefault = portfolioState.labs.find((lab) => lab.selected);
    if (serverDefault) selectedLabId = serverDefault.lab_id;
  }
  markSelected();
  text("observe-lab-title", labDisplayName(selectedLabId || controlState.lab_id));
  portfolioBudget = portfolioState.labs.reduce(
    (sum, lab) => ({
      spent: sum.spent + Number(lab.budget?.spent || 0),
      cap: sum.cap + Number(lab.budget?.cap || 0),
    }),
    { spent: 0, cap: 0 },
  );
  renderLabRail();
  renderLabTabs();
  renderBudget();
  renderNetwork();
}

async function refreshPortfolio() {
  renderPortfolio(await getJSON("/api/labs"));
}

async function selectPortfolioLab(labId, openObserver) {
  selectedLabId = labId;
  writeStored("efferents-selected-lab", labId);
  markSelected();
  if (openObserver) window.location.hash = "observe";
  const info = await getJSON(labPath("control"));
  renderControl(info);
  renderLabRail();
  renderLabTabs();
  renderNetwork();
  await refreshObserver();
}

const CLAMP_THRESHOLD = 260;

function applyThesisClamp(id) {
  const copy = document.getElementById(id);
  const block = copy.closest(".thesis-block");
  const long = (copy.textContent || "").length > CLAMP_THRESHOLD;
  let button = block.querySelector(".clamp-toggle");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.className = "clamp-toggle";
    button.addEventListener("click", () => {
      const expanded = block.dataset.expanded !== "true";
      block.dataset.expanded = String(expanded);
      copy.classList.toggle("clamped", !expanded);
      button.textContent = expanded ? "Collapse" : "Read in full";
    });
    block.appendChild(button);
  }
  const expanded = block.dataset.expanded === "true";
  copy.classList.toggle("clamped", long && !expanded);
  button.hidden = !long;
  if (!expanded) button.textContent = "Read in full";
}

function renderState(state) {
  if (!controlState.connected) return;
  setRuntimeStatus(state.status || controlState.status);

  labBudget = {
    spent: Number(state.budget?.spent || 0),
    cap: Number(state.budget?.cap || 0),
  };
  renderBudget();

  const hypothesis = state.hypothesis || {};
  text("student", hypothesis.student ? `student / ${hypothesis.student}` : "student / —");
  const question = hypothesis.question || "No open campaign.";
  const claim = hypothesis.claim || "No claim.";
  text("question", question);
  text("claim", question.replace(/\s+/g, " ").trim() === claim.replace(/\s+/g, " ").trim()
    ? "Matches the active hypothesis above."
    : claim);
  text("falsifier", hypothesis.falsifier || "No falsifier.");
  applyThesisClamp("claim");
  applyThesisClamp("falsifier");
}

function renderRuns(data) {
  const headline = data.headline || { column: "metric", direction: "min" };
  const direction = headline.direction === "max" ? "max" : "min";
  const directionLabel = direction === "max" ? "higher is better" : "lower is better";
  const runs = Array.isArray(data.runs) ? data.runs : [];
  const history = data.history || {};
  const observedRuns = runs.filter((run) => Number.isFinite(Number(run.value)));
  const eligibleRuns = observedRuns.filter((run) => run.eligible !== false);
  const eligibleValues = eligibleRuns.map((run) => Number(run.value));
  const recentBest = eligibleValues.length
    ? (direction === "max" ? Math.max(...eligibleValues) : Math.min(...eligibleValues))
    : null;
  const best = Number.isFinite(Number(history.best)) ? Number(history.best) : recentBest;
  const latest = eligibleRuns.length ? Number(eligibleRuns[0].value) : null;
  const bestRunId = history.best_run_id ||
    eligibleRuns.find((run) => Number(run.value) === best)?.run_id || "";
  const median = quantile(eligibleValues, 0.5);
  const lowerQuartile = quantile(eligibleValues, 0.25);
  const upperQuartile = quantile(eligibleValues, 0.75);
  const iqr = lowerQuartile == null || upperQuartile == null
    ? null
    : upperQuartile - lowerQuartile;
  const excludedCount = runs.filter((run) => run.eligible === false).length;

  text("metric-label", headline.column || "Headline metric");
  text("metric-direction", directionLabel);
  text("run-metric-header", headline.column || "Result");
  text("run-count", `${runs.length} recent / ${Number(history.total || runs.length)} total`);
  text("metric-best", formatMetric(best));
  text("metric-latest", formatMetric(latest));
  text("metric-eligible", `${eligibleRuns.length} / ${runs.length}`);
  text("metric-median", formatMetric(median));
  text("metric-iqr", formatMetric(iqr));
  const bestRunElement = document.getElementById("metric-best-run");
  bestRunElement.textContent = compactRunId(bestRunId);
  bestRunElement.title = bestRunId;

  if (latest == null || best == null) {
    text("metric-delta", "—");
  } else {
    const gap = direction === "max" ? best - latest : latest - best;
    text("metric-delta", Math.abs(gap) < Number.EPSILON ? "at best" : formatMetric(gap));
  }

  const tbody = document.querySelector("#runs tbody");
  tbody.innerHTML = "";
  const firstBestIndex = runs.findIndex((run) =>
    run.eligible !== false && Number.isFinite(Number(run.value)) && Number(run.value) === best
  );
  runs.forEach((run, index) => {
    const numericValue = Number(run.value);
    const hasValue = Number.isFinite(numericValue);
    const eligible = hasValue && run.eligible !== false;
    const excluded = run.eligible === false;
    const tiesBest = eligible && best != null && numericValue === best;
    const isBest = tiesBest && index === firstBestIndex;
    const row = document.createElement("tr");
    row.className = isBest ? "is-best" : excluded ? "is-excluded" : "";
    const failures = Array.isArray(run.constraint_failures)
      ? run.constraint_failures.join("; ")
      : "";
    if (failures) row.title = failures;
    const validity = isBest
      ? "best"
      : tiesBest
        ? "ties best"
        : excluded
          ? "excluded"
          : hasValue
            ? "eligible"
            : "missing";
    row.innerHTML =
      `<td>${String(runs.length - index).padStart(2, "0")}</td>` +
      `<td class="run-id" title="${esc(run.run_id || "")}">${esc(run.run_id || "—")}</td>` +
      `<td title="${esc(run.started_at || "")}">${esc(formatTimestamp(run.started_at))}</td>` +
      `<td class="metric-cell">${esc(formatMetric(run.value))}</td>` +
      `<td><span class="signal-tag${isBest ? " best" : excluded ? " excluded" : ""}">` +
      `${validity}</span></td>`;
    tbody.appendChild(row);
  });

  if (!runs.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="5"><div class="empty-state">No runs</div></td>';
    tbody.appendChild(row);
  }

  renderTrend(Array.isArray(data.series) ? data.series : [], direction, {
    metric: headline.column || "metric",
    eligible: eligibleRuns.length,
    excluded: excludedCount,
    median,
  });
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderTrend(series, direction, summary = {}) {
  const svg = document.getElementById("trend");
  svg.innerHTML = "";
  const finiteSeries = series.filter((point) => Number.isFinite(Number(point.value)));
  svg.setAttribute(
    "aria-label",
    `${summary.metric || "metric"} across ${finiteSeries.length} eligible observations`,
  );

  if (!finiteSeries.length) {
    text("trend-caption", "No eligible metric observations");
    text("metric-range", "—");
    return;
  }

  const width = 600;
  const height = 180;
  const padding = { top: 14, right: 18, bottom: 24, left: 62 };
  const values = finiteSeries.map((point) => Number(point.value));
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawSpan = rawMax - rawMin;
  const span = rawSpan || Math.max(Math.abs(rawMax) * 0.1, 1);
  const min = rawMin - span * 0.08;
  const max = rawMax + span * 0.08;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + (chartHeight / 4) * index;
    svg.appendChild(svgElement("line", {
      x1: padding.left,
      x2: width - padding.right,
      y1: y,
      y2: y,
      class: "grid-line",
    }));
    const tick = svgElement("text", {
      x: padding.left - 7,
      y: y + 3,
      "text-anchor": "end",
    });
    tick.textContent = formatMetric(max - ((max - min) / 4) * index);
    svg.appendChild(tick);
  }

  const coordinates = finiteSeries.map((point, index) => {
    const x = finiteSeries.length === 1
      ? padding.left + chartWidth / 2
      : padding.left + (index / (finiteSeries.length - 1)) * chartWidth;
    const y = padding.top + (1 - ((Number(point.value) - min) / (max - min))) * chartHeight;
    return { x, y, value: Number(point.value) };
  });

  if (coordinates.length > 1) {
    const linePoints = coordinates.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
    const areaPoints = `${padding.left},${height - padding.bottom} ${linePoints} ` +
      `${width - padding.right},${height - padding.bottom}`;
    svg.appendChild(svgElement("polygon", { points: areaPoints, class: "area" }));
    svg.appendChild(svgElement("polyline", { points: linePoints, class: "trend-line" }));
  }

  const best = direction === "max" ? rawMax : rawMin;
  coordinates.forEach((point) => {
    const isBest = point.value === best;
    svg.appendChild(svgElement("circle", {
      cx: point.x,
      cy: point.y,
      r: isBest ? 3.7 : 2.5,
      class: `point${isBest ? " best" : ""}`,
    }));
  });

  if (summary.median != null) {
    const medianY = padding.top +
      (1 - ((Number(summary.median) - min) / (max - min))) * chartHeight;
    svg.appendChild(svgElement("line", {
      x1: padding.left,
      x2: width - padding.right,
      y1: medianY,
      y2: medianY,
      class: "reference-line",
    }));
    const medianLabel = svgElement("text", {
      x: width - padding.right,
      y: medianY - 4,
      "text-anchor": "end",
      class: "reference-label",
    });
    medianLabel.textContent = `median ${formatMetric(summary.median)}`;
    svg.appendChild(medianLabel);
  }

  const startLabel = svgElement("text", {
    x: padding.left,
    y: height - 6,
    "text-anchor": "start",
  });
  startLabel.textContent = formatTimestamp(finiteSeries[0].started_at, true);
  svg.appendChild(startLabel);
  const endLabel = svgElement("text", {
    x: width - padding.right,
    y: height - 6,
    "text-anchor": "end",
  });
  endLabel.textContent = formatTimestamp(finiteSeries[finiteSeries.length - 1].started_at, true);
  svg.appendChild(endLabel);

  text(
    "trend-caption",
    `${finiteSeries.length} eligible / chronological` +
      (summary.excluded ? ` · ${summary.excluded} excluded` : ""),
  );
  text("metric-range", `range ${formatMetric(rawMin)} — ${formatMetric(rawMax)}`);
}

function evidenceComparisonKey(record, axis) {
  const shared = Object.entries(record.dimensions || {})
    .filter(([key]) => key !== axis)
    .sort(([left], [right]) => left.localeCompare(right));
  return JSON.stringify([record.started_at || "", shared]);
}

function groupEvidenceRecords(records, comparison) {
  const axis = comparison?.axis;
  if (!axis) return records.map((record) => ({ kind: "single", records: [record] }));

  const candidates = new Map();
  records.forEach((record) => {
    const value = record.dimensions?.[axis];
    if (value == null) return;
    const key = evidenceComparisonKey(record, axis);
    if (!candidates.has(key)) candidates.set(key, []);
    candidates.get(key).push(record);
  });

  const pairedKeys = new Set(
    [...candidates.entries()]
      .filter(([, group]) => new Set(group.map((record) => record.dimensions[axis])).size > 1)
      .map(([key]) => key),
  );
  const emitted = new Set();
  const groups = [];
  records.forEach((record) => {
    const value = record.dimensions?.[axis];
    const key = value == null ? null : evidenceComparisonKey(record, axis);
    if (key && pairedKeys.has(key)) {
      if (!emitted.has(key)) {
        groups.push({ kind: "comparison", records: candidates.get(key) });
        emitted.add(key);
      }
      return;
    }
    groups.push({ kind: "single", records: [record] });
  });
  return groups;
}

function evidenceVariantLabel(value, comparison) {
  return comparison?.labels?.[value] || String(value).replace(/[_-]+/g, " ");
}

function renderEvidenceRecord(record, metricPanels, hiddenDimensions = new Set()) {
  const dimensions = Object.entries(record.dimensions || {})
    .filter(([key]) => !hiddenDimensions.has(key));
  const failures = Array.isArray(record.constraint_failures)
    ? record.constraint_failures
    : [];
  const artifact = (record.artifacts || [])[0];
  const metricRows = metricPanels
    .filter((metric) => record.metrics?.[metric.column] != null)
    .map((metric) =>
      `<div><dt title="${esc(metric.label)}">${esc(metric.label)}</dt>` +
      `<dd>${esc(formatMetric(record.metrics[metric.column]))}</dd></div>`
    ).join("");
  return `<article class="evidence-record ${record.eligible ? "is-eligible" : "is-excluded"}">` +
    (artifact
      ? `<a class="evidence-artifact" href="${esc(artifact.url)}" target="_blank" rel="noopener" ` +
        `aria-label="Open ${esc(record.name)} image at full resolution">` +
        `<img src="${esc(artifact.url)}" loading="lazy" alt="${esc(record.name)} visual result"></a>`
      : "") +
    `<div class="evidence-record-body"><div class="evidence-record-head">` +
    `<strong title="${esc(record.run_id)}">${esc(record.name || compactRunId(record.run_id))}</strong>` +
    `<span class="evidence-validity">${record.eligible ? "eligible" : "excluded"}</span></div>` +
    (dimensions.length
      ? `<div class="evidence-dimensions">` + dimensions.map(([key, value]) =>
        `<span>${esc(key)}=<strong>${esc(value)}</strong></span>`).join("") + `</div>`
      : "") +
    `<dl class="evidence-metrics">${metricRows}</dl>` +
    (failures.length
      ? `<p class="evidence-failure">${failures.map(esc).join(" · ")}</p>`
      : "") +
    `</div></article>`;
}

function renderEvidenceComparison(group, metricPanels, comparison) {
  const axis = comparison.axis;
  const order = Array.isArray(comparison.order) ? comparison.order : [];
  const records = [...group.records].sort((left, right) => {
    const leftIndex = order.indexOf(String(left.dimensions?.[axis]));
    const rightIndex = order.indexOf(String(right.dimensions?.[axis]));
    if (leftIndex < 0 && rightIndex < 0) return 0;
    if (leftIndex < 0) return 1;
    if (rightIndex < 0) return -1;
    return leftIndex - rightIndex;
  });
  const shared = Object.entries(records[0].dimensions || {})
    .filter(([key]) => key !== axis);
  const hidden = new Set([axis, ...shared.map(([key]) => key)]);
  const labels = records.map((record) =>
    evidenceVariantLabel(record.dimensions?.[axis], comparison));
  const sharedLabel = shared.map(([key, value]) =>
    `${key}=${value}`).join(" · ");
  return `<section class="evidence-comparison" aria-label="Matched comparison: ${esc(labels.join(" versus "))}">` +
    `<header class="evidence-comparison-head"><div>` +
    `<span>Matched comparison</span><strong>${esc(sharedLabel || "shared run context")}</strong></div>` +
    `<small>same ${esc(shared.map(([key]) => key).join(" / ") || "context")} · only ${esc(axis)} changes</small>` +
    `</header><div class="evidence-comparison-grid">` +
    records.map((record, index) => {
      const value = record.dimensions?.[axis];
      const label = evidenceVariantLabel(value, comparison);
      return `<div class="evidence-variant"><header class="evidence-variant-head">` +
        `<span>${String(index + 1).padStart(2, "0")}</span><strong>${esc(label)}</strong>` +
        `<small>${esc(axis)}=${esc(value)}</small></header>` +
        renderEvidenceRecord(record, metricPanels, hidden) + `</div>`;
    }).join("") + `</div></section>`;
}

function renderEvidence(data) {
  const panel = document.getElementById("evidence-panel");
  const records = Array.isArray(data?.records) ? data.records : [];
  const metricPanels = Array.isArray(data?.panels) ? data.panels : [];
  const constraints = Array.isArray(data?.constraints) ? data.constraints : [];
  const comparison = data?.comparison || {};
  const groups = groupEvidenceRecords(records, comparison);
  const matched = groups.filter((group) => group.kind === "comparison").length;
  const standalone = groups.length - matched;
  panel.hidden = records.length === 0;
  text(
    "evidence-count",
    `${matched} matched / ${standalone} standalone / ${Number(data?.artifact_count || 0)} images`,
  );
  if (!records.length) return;

  const gates = document.getElementById("evidence-gates");
  gates.innerHTML = constraints.length
    ? constraints.map((constraint) =>
      `<span class="evidence-gate"><strong>${esc(constraint.label || constraint.column)}</strong> ` +
      `${esc(constraint.column)} ${esc(constraint.op)} ${esc(formatMetric(constraint.value))}</span>`
    ).join("")
    : '<span class="evidence-axis"><strong>No eligibility gates</strong></span>';
  gates.insertAdjacentHTML(
    "beforeend",
    `<span class="evidence-axis"><strong>${metricPanels.length}</strong> configured metrics</span>`,
  );

  const gallery = document.getElementById("evidence-gallery");
  gallery.innerHTML = groups.map((group) => group.kind === "comparison"
    ? renderEvidenceComparison(group, metricPanels, comparison)
    : renderEvidenceRecord(group.records[0], metricPanels)
  ).join("");
}

function formatCI(ci) {
  return Array.isArray(ci) && ci.length === 2
    ? `[${formatMetric(ci[0])}, ${formatMetric(ci[1])}]`
    : "—";
}

function renderVerdict(data) {
  const falsifiers = Array.isArray(data?.falsifiers) ? data.falsifiers : [];
  const buckets = Array.isArray(data?.buckets) ? data.buckets : [];
  const columns = Array.isArray(data?.columns) ? data.columns : [];
  const axes = Array.isArray(data?.axes) ? data.axes : [];
  const comparison = data?.comparison || {};
  const runs = Number(data?.n_runs || 0);
  text("verdict-count", `${runs} succeeded ${runs === 1 ? "run" : "runs"}`);

  const line = document.getElementById("verdict-line");
  line.textContent = data?.line || "verdict: undecided";
  line.classList.toggle("falsified", data?.verdict === "falsified");

  const falsifierBody = document.querySelector("#falsifiers tbody");
  falsifierBody.innerHTML = falsifiers.length
    ? falsifiers.map((f) =>
      `<tr><td>${esc(f.id)}</td><td>${esc(f.bucket)}</td>` +
      `<td class="status-${esc(f.status)}">${esc(f.status === "insufficient_data" ? "insufficient" : f.status)}</td>` +
      `<td class="rule-cell">${esc(f.rule)} — ${esc(f.detail)}</td></tr>`
    ).join("")
    : '<tr><td colspan="4" class="empty-state">No falsifiers declared in lab.yaml</td></tr>';

  const armLabel = (arm) => comparison.labels?.[arm] || arm;
  const arms = [];
  buckets.forEach((bucket) => Object.keys(bucket.arms || {}).forEach((arm) => {
    if (!arms.includes(arm)) arms.push(arm);
  }));
  const pairedMetrics = [];
  buckets.forEach((bucket) => Object.keys(bucket.paired || {}).forEach((metric) => {
    if (!pairedMetrics.includes(metric)) pairedMetrics.push(metric);
  }));
  const axisLabel = axes.length ? axes.join(" × ") : "all runs";
  text(
    "buckets-meta",
    `${buckets.length} ${buckets.length === 1 ? "bucket" : "buckets"} by ${axisLabel}` +
      (comparison.axis && arms.length ? ` · ${arms.map(armLabel).join(" vs ")}` : ""),
  );

  const head = [`<th scope="col">Bucket</th>`, `<th scope="col">n</th>`];
  columns.forEach((c) => head.push(`<th scope="col" class="metric-head">median ${esc(c.label || c.column)}</th>`));
  // Only metrics some arm actually reports; the flat panel medians cover the rest.
  const armColumns = columns.filter((c) =>
    buckets.some((bucket) => arms.some((arm) => bucket.arms?.[arm]?.[c.column]))
  );
  arms.forEach((arm) => armColumns.forEach((c) =>
    head.push(`<th scope="col" class="metric-head">${esc(armLabel(arm))} ${esc(c.column)}</th>`)
  ));
  pairedMetrics.forEach((metric) =>
    head.push(`<th scope="col" class="metric-head">Δ${esc(metric)} · 95% CI</th>`)
  );
  document.querySelector("#buckets thead tr").innerHTML = head.join("");

  const bucketBody = document.querySelector("#buckets tbody");
  bucketBody.innerHTML = buckets.length
    ? buckets.map((bucket) => {
      const cells = [`<td>${esc(bucket.label)}</td>`, `<td>${esc(bucket.n)}</td>`];
      columns.forEach((c) => cells.push(
        `<td class="metric-cell">${esc(formatMetric(bucket.columns?.[c.column]?.median))}</td>`
      ));
      arms.forEach((arm) => armColumns.forEach((c) => cells.push(
        `<td class="metric-cell">${esc(formatMetric(bucket.arms?.[arm]?.[c.column]?.median))}</td>`
      )));
      pairedMetrics.forEach((metric) => {
        const p = bucket.paired?.[metric];
        cells.push(`<td class="metric-cell">${p
          ? `${esc(formatMetric(p.median))} <span class="ci">${esc(formatCI(p.ci95))} n=${esc(p.n)}</span>`
          : "—"}</td>`);
      });
      return `<tr>${cells.join("")}</tr>`;
    }).join("")
    : `<tr><td colspan="${head.length}" class="empty-state">No succeeded runs</td></tr>`;
}

function renderPapers(papers) {
  const records = Array.isArray(papers) ? papers : [];
  const element = document.getElementById("papers");
  text("paper-count", `${records.length} ${records.length === 1 ? "record" : "records"}`);

  if (!records.length) {
    element.innerHTML = '<div class="empty-state">No cleared papers</div>';
    return;
  }

  element.innerHTML = records.map((paper) =>
    `<article class="paper-record">` +
      `<div class="record-glyph" aria-hidden="true">↗</div>` +
      `<div><div class="paper-title">${esc(paper.title || "Untitled artifact")}</div>` +
      `<div class="paper-meta">${esc(paper.campaign_id || "no campaign")} · ${esc(paper.published_at || "undated")}</div></div>` +
      `<div class="paper-status">${esc(paper.status || "draft")}</div>` +
    `</article>`
  ).join("");
}

function renderActivity(activities) {
  const records = Array.isArray(activities) ? activities : [];
  const element = document.getElementById("activity");

  if (!records.length) {
    element.innerHTML = '<div class="empty-state">No agent events</div>';
    return;
  }

  element.innerHTML = records.map((activity) => {
    const body = String(activity.body || "").replace(/\s+/g, " ").trim();
    return `<article class="activity-item">` +
      `<time class="activity-when">${esc(formatTimestamp(activity.timestamp, true))} UTC</time>` +
      `<div class="activity-content"><div class="activity-title" title="${esc(activity.title)}">${esc(activity.title)}</div>` +
      (body ? `<div class="activity-body">${esc(body)}</div>` : "") +
      `</div></article>`;
  }).join("");
}

async function refreshObserver() {
  if (!controlState.connected) return;
  if (document.hidden || currentRoute() !== "observe") return;
  const scoped = Boolean(selectedLabId);
  const requests = [
    ["/api/state", renderState],
    ["/api/runs", renderRuns],
    ["/api/evidence", renderEvidence],
    ["/api/verdict", renderVerdict],
    ["/api/papers", renderPapers],
    ["/api/activity", renderActivity],
  ];
  const results = await Promise.allSettled(
    requests.map(async ([path, renderer]) => {
      const url = scoped ? labPath(path.replace("/api/", "")) : path;
      return renderer(await getJSON(url));
    })
  );
  results
    .filter((result) => result.status === "rejected")
    .forEach((result) => console.error(result.reason));
}

async function refresh() {
  try {
    const session = await getJSON("/api/control");
    if (session.csrf_token) csrfToken = session.csrf_token;
    controlState.mode = session.mode || "local";
    await refreshPortfolio();
    if (selectedLabId && portfolioState.labs.some((lab) => lab.lab_id === selectedLabId)) {
      renderControl(await getJSON(labPath("control")));
    } else {
      selectedLabId = null;
      renderControl(session);
    }
    await refreshObserver();
  } catch (error) {
    console.error(error);
  }
}

function initIntakeTabs() {
  const tabs = document.querySelectorAll("[data-intake-tab]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((other) => {
        const active = other === tab;
        other.setAttribute("aria-selected", String(active));
      });
      document.querySelectorAll("[data-intake-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.intakePanel !== tab.dataset.intakeTab;
      });
    });
  });

  const copy = document.getElementById("copy-command");
  copy.addEventListener("click", async () => {
    const command = document.getElementById("agent-command").textContent;
    try {
      await navigator.clipboard.writeText(command);
      copy.textContent = "Copied";
    } catch (error) {
      copy.textContent = "Select + copy";
    }
    setTimeout(() => { copy.textContent = "Copy"; }, 1600);
  });
}

function initConnectForm() {
  const form = document.getElementById("connect-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const source = document.getElementById("github-source").value.trim();
    const button = document.getElementById("connect-submit");
    isConnecting = true;
    button.disabled = true;
    button.textContent = "Connecting…";
    setContractState({}, "checking");
    showMessage("connect-message", "Checking out and validating the submission contract…");
    try {
      const info = await postJSON("/api/connect", { source });
      await refreshPortfolio();
      renderControl(info);
      showMessage(
        "connect-message",
        info.routing?.applied
          ? `${labDisplayName(info.lab_id)} · joined as student ${info.routing.student_id}`
          : `${labDisplayName(info.lab_id)} connected · not executed`,
        "success",
      );
      window.location.hash = "observe";
      await Promise.all([refreshPortfolio(), refreshObserver()]);
    } catch (error) {
      setContractState({});
      showMessage("connect-message", error.message, "error");
    } finally {
      isConnecting = false;
      button.disabled = false;
      button.textContent = "Connect lab";
    }
  });
}

function initOnboarding() {
  const mode = document.getElementById("onboard-mode");
  mode.addEventListener("change", () => { document.getElementById("onboard-goal-field").hidden = mode.value !== "shared"; });
  const scopeDialog = document.getElementById("starter-scope-dialog");
  const inspectScope = () => {
    scopeDialog.close();
    window.location.hash = "network";
  };
  scopeDialog.addEventListener("cancel", (event) => { event.preventDefault(); inspectScope(); });
  document.getElementById("starter-scope-inspect").addEventListener("click", inspectScope);
  document.getElementById("starter-scope-run").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    showMessage("starter-scope-message", "Running three bounded CPU experiments…");
    try {
      await postJSON("/api/lab/trial", {runs: 3});
      scopeDialog.close();
      window.location.hash = "network";
      await refreshPortfolio();
    } catch (error) {
      showMessage("starter-scope-message", error.message, "error");
    } finally { button.disabled = false; }
  });
  const submit = async (run) => {
    const buttons = [document.getElementById("onboard-run"), document.getElementById("onboard-create")];
    buttons.forEach((button) => { button.disabled = true; });
    showMessage("onboard-message", "Recording choices and creating your lab…");
    try {
      const goal = mode.value === "shared" ? (document.getElementById("onboard-goal").value.trim() || "Reduce congestion") : "";
      const info = await postJSON("/api/onboard", {
        confirmed: true, run: false, goal, idea: document.getElementById("onboard-idea").value,
        starter: document.getElementById("onboard-starter").value,
        approach: document.getElementById("onboard-approach").value,
        exchange: document.getElementById("onboard-exchange").checked,
      });
      await refreshPortfolio();
      renderControl(info);
      showMessage("onboard-message", `${labDisplayName(info.lab_id)} · ${info.decisions.starter} · choices saved in context/onboarding.json`, "success");
      if (run) {
        const submittedIdea = String(info.decisions.idea || "").trim();
        document.getElementById("starter-scope-idea-block").hidden = !submittedIdea;
        text("starter-scope-idea", submittedIdea);
        text("starter-scope-claim", info.decisions.experiment_claim || "Inspect the lab hypothesis for the executable claim.");
        text("starter-scope-notice", info.decisions.scope_notice || "The selected starter defines the first experiment's scope.");
        text("starter-scope-message", "");
        scopeDialog.showModal();
      } else {
        window.location.hash = "network";
        await refreshPortfolio();
      }
    } catch (error) { showMessage("onboard-message", error.message, "error"); }
    finally { buttons.forEach((button) => { button.disabled = false; }); }
  };
  document.getElementById("onboard-form").addEventListener("submit", (event) => { event.preventDefault(); submit(true); });
  document.getElementById("onboard-create").addEventListener("click", () => submit(false));
  document.getElementById("trial-lab").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      await postJSON("/api/lab/trial", { runs: 3 });
      window.location.hash = "network";
    } catch (error) { console.error(error); }
    finally { event.target.disabled = false; }
  });
}

function initSteeringForm() {
  const form = document.getElementById("steer-form");
  const message = document.getElementById("steer-message");
  message.addEventListener("input", () => text("steer-count", message.value.length));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    showMessage("steer-message-state", "Recording direction in the steering ledger…");
    try {
      const result = await postJSON(selectedLabId ? labPath("steer") : "/api/steer", {
        message: message.value,
        mode: document.getElementById("steer-mode").value,
      });
      message.value = "";
      text("steer-count", "0");
      renderSteering(result.steering);
      showMessage(
        "steer-message-state",
        result.status === "running"
          ? "Direction recorded · next agent pass"
          : "Direction recorded · read on start",
        "success",
      );
    } catch (error) {
      showMessage("steer-message-state", error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });
}

const RUNTIME_COPY = {
  start: {
    kicker: "Local execution", title: "Start lab?",
    copy: "Up to 3 agent iterations · local repository commands · configured LLM budget. Missing credit or credentials stops this run.",
    label: "Authorize this local run.", button: "Confirm start",
    progress: "Starting the daemon…",
  },
  stop: {
    kicker: "Stop execution", title: "Stop lab?",
    copy: "Stop after current process · preserve written evidence",
    label: "Authorize this stop request.", button: "Confirm stop",
    progress: "Stopping the daemon…",
  },
  pause: {
    kicker: "Pause spending", title: "Pause lab?",
    copy: "Recorded as owner steering · takes effect at the next agent step · evidence preserved",
    label: "Authorize this pause.", button: "Confirm pause",
    progress: "Queuing the pause…",
  },
  resume: {
    kicker: "Resume spending", title: "Resume lab?",
    copy: "Lifts the owner pause · recorded as owner steering",
    label: "Authorize this resume.", button: "Confirm resume",
    progress: "Queuing the resume…",
  },
};

function openRuntimeDialog(action) {
  runtimeAction = action;
  const copy = RUNTIME_COPY[action] || RUNTIME_COPY.start;
  text("runtime-dialog-kicker", copy.kicker);
  text("runtime-dialog-title", copy.title);
  text("runtime-dialog-copy", copy.copy);
  text("runtime-confirm-label", copy.label);
  text("runtime-confirm-button", copy.button);
  const checkbox = document.getElementById("runtime-confirm-check");
  checkbox.checked = false;
  document.getElementById("runtime-confirm-button").disabled = true;
  showMessage("runtime-dialog-message");
  document.getElementById("runtime-dialog").showModal();
}

function initRuntimeControls() {
  const dialog = document.getElementById("runtime-dialog");
  const checkbox = document.getElementById("runtime-confirm-check");
  const confirm = document.getElementById("runtime-confirm-button");
  document.getElementById("start-lab").addEventListener("click", () => openRuntimeDialog("start"));
  document.getElementById("stop-lab").addEventListener("click", () => openRuntimeDialog("stop"));
  document.getElementById("pause-lab").addEventListener("click", () => openRuntimeDialog("pause"));
  document.getElementById("resume-lab").addEventListener("click", () => openRuntimeDialog("resume"));
  checkbox.addEventListener("change", () => {
    confirm.disabled = !checkbox.checked;
  });
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    showMessage("runtime-dialog-message", (RUNTIME_COPY[runtimeAction] || {}).progress || "");
    try {
      const legacy = `/api/lab/${runtimeAction}`;
      const info = await postJSON(selectedLabId ? labPath(runtimeAction) : legacy, { confirmed: true });
      renderControl(info);
      dialog.close();
      await refreshObserver();
    } catch (error) {
      showMessage("runtime-dialog-message", error.message, "error");
      confirm.disabled = false;
    }
  });
}

function initPanelToggles() {
  document.querySelectorAll("[data-panel-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = button.closest(".panel");
      const collapsed = panel.classList.toggle("collapsed");
      button.setAttribute("aria-expanded", String(!collapsed));
      button.textContent = collapsed ? "Show" : "Hide";
    });
  });
}

initRouting();
initLabRailToggle();
initMapPanZoom();
initPanelToggles();
initIntakeTabs();
initConnectForm();
initOnboarding();
initSteeringForm();
initRuntimeControls();
refresh();
setInterval(refresh, 4000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
