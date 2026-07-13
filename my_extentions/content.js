// ─── Config ──────────────────────────────────────────────────────
const CHAINLIT_BASE_URL = "http://localhost:8000";
const PANEL_WIDTH = 650;
const PANEL_MIN_WIDTH = 320;
const PANEL_MAX_WIDTH = 1100;
const PAGE_TEXT_MAX_CHARS = 12000;

// ─── State ───────────────────────────────────────────────────────
let panelOpen = false;
let panelWidth = PANEL_WIDTH;
let isResizing = false;

function applyPanelWidth() {
  document.documentElement.style.setProperty("--chainlit-panel-width", `${panelWidth}px`);
}

function clampPanelWidth(nextWidth) {
  return Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, nextWidth));
}

function onResizeMove(event) {
  if (!isResizing) return;

  const nextWidth = clampPanelWidth(window.innerWidth - event.clientX);
  panelWidth = nextWidth;
  applyPanelWidth();
}

function onResizeUp() {
  isResizing = false;
  document.body.classList.remove("chainlit-resizing");
  document.removeEventListener("mousemove", onResizeMove);
  document.removeEventListener("mouseup", onResizeUp);
}

function startResize(event) {
  event.preventDefault();
  isResizing = true;
  document.body.classList.add("chainlit-resizing");
  document.addEventListener("mousemove", onResizeMove);
  document.addEventListener("mouseup", onResizeUp);
}

// ─── 1. Create the floating button ───────────────────────────────
function createFloatingButton() {
  const btn = document.createElement("button");
  btn.id = "ask-page-btn";
  btn.title = "Ask about this page";

  const icon = document.createElement("img");
  icon.src = chrome.runtime.getURL("icons/gateway.svg");
  icon.alt = "Open question gateway";
  icon.className = "ask-page-btn-icon";
  btn.appendChild(icon);

  btn.addEventListener("click", togglePanel);
  document.body.appendChild(btn);
}

// ─── 2. Create the slide-in panel ────────────────────────────────
function createPanel() {
  // Panel container
  const panel = document.createElement("div");
  panel.id = "chainlit-panel";

  // Left edge resize handle
  const resizeHandle = document.createElement("div");
  resizeHandle.id = "chainlit-resize-handle";
  resizeHandle.title = "Drag to resize";

  // Header bar
  const header = document.createElement("div");
  header.id = "chainlit-panel-header";
  header.innerHTML = `
    <span>🤖 Ask This Page</span>
    <button id="chainlit-close-btn" title="Close">✕</button>
  `;

  // The iframe — Chainlit loads in here
  const iframe = document.createElement("iframe");
  iframe.id = "chainlit-iframe";
  iframe.src = "about:blank";   // stays blank until user clicks the button

  panel.appendChild(resizeHandle);
  panel.appendChild(header);
  panel.appendChild(iframe);
  document.body.appendChild(panel);

  // Close button
  document.getElementById("chainlit-close-btn")
    .addEventListener("click", closePanel);

  resizeHandle.addEventListener("mousedown", startResize);
}

// ─── 3. Extract page text ─────────────────────────────────────────
function cleanText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function smartTruncate(text, maxChars) {
  if (!text || text.length <= maxChars) return text;

  const hardCut = text.slice(0, maxChars);

  // Prefer ending at sentence boundary, then whitespace, then hard cut.
  const sentenceCut = Math.max(
    hardCut.lastIndexOf(". "),
    hardCut.lastIndexOf("! "),
    hardCut.lastIndexOf("? ")
  );
  if (sentenceCut > maxChars * 0.6) {
    return hardCut.slice(0, sentenceCut + 1).trim();
  }

  const wordCut = hardCut.lastIndexOf(" ");
  if (wordCut > maxChars * 0.6) {
    return hardCut.slice(0, wordCut).trim();
  }

  return hardCut.trim();
}

function getVisibleTextFromRoot(root) {
  if (!root) return "";

  // innerText is often more reliable for dynamic/visible content than text node walking.
  const direct = cleanText(root.innerText || root.textContent || "");
  if (direct.length > 40) return direct;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const chunks = [];

  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent) continue;

    const tag = parent.tagName;
    if (["SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "SVG"].includes(tag)) continue;
    if (parent.closest("nav, footer, header, aside")) continue;
    if (parent.getAttribute("aria-hidden") === "true") continue;

    const text = cleanText(node.nodeValue || "");
    if (text.length > 1) chunks.push(text);
  }

  return cleanText(chunks.join(" "));
}

function getPageText() {
  const prioritizedRoots = [
    document.querySelector("article"),
    document.querySelector("main"),
    document.querySelector('[role="main"]'),
    document.body,
    document.documentElement,
  ];

  for (const root of prioritizedRoots) {
    const text = getVisibleTextFromRoot(root);
    if (text.length > 40) {
      return smartTruncate(text, PAGE_TEXT_MAX_CHARS);
    }
  }

  // Final fallback so the backend always gets some context.
  const metaDescription = document.querySelector('meta[name="description"]')?.getAttribute("content") || "";
  return smartTruncate(cleanText(`${document.title} ${metaDescription}`), PAGE_TEXT_MAX_CHARS);
}

// ─── 4. Build the Chainlit URL with page context ──────────────────
function buildPageContext() {
  return {
    page_url: window.location.href,
    page_title: document.title,
    page_text: getPageText(),
  };
}

function buildChainlitUrlWithQuery(context) {
  const pageUrl = encodeURIComponent(context.page_url || "");
  const pageTitle = encodeURIComponent(context.page_title || "");
  const pageText = encodeURIComponent(context.page_text || "");

  return `${CHAINLIT_BASE_URL}?page_url=${pageUrl}&page_title=${pageTitle}&page_text=${pageText}`;
}

async function sendContextToBackend(context) {
  const endpoint = `${CHAINLIT_BASE_URL}/ext/context`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(context),
  });

  if (!response.ok) {
    throw new Error(`Context POST failed (${response.status})`);
  }
}

function buildChainlitUrl() {
  const context = buildPageContext();

  console.debug("[Ask This Page] Scraped context", {
    pageUrl: context.page_url,
    pageTitle: context.page_title,
    pageTextLength: context.page_text.length,
    pageTextPreview: context.page_text.slice(0, 120),
  });

  return buildChainlitUrlWithQuery(context);
}

// ─── 5. Open / close logic ────────────────────────────────────────
async function openPanel() {
  const panel  = document.getElementById("chainlit-panel");
  const iframe = document.getElementById("chainlit-iframe");
  const btn    = document.getElementById("ask-page-btn");

  const context = buildPageContext();

  try {
    await sendContextToBackend(context);
    iframe.src = `${CHAINLIT_BASE_URL}?t=${Date.now()}`;
  } catch (error) {
    console.warn("[Ask This Page] Context POST failed, using query fallback", error);
    iframe.src = buildChainlitUrlWithQuery(context);
  }

  panel.classList.add("open");
  document.body.classList.add("chainlit-panel-open");
  btn.classList.add("open");
  panelOpen = true;
}

function closePanel() {
  const panel = document.getElementById("chainlit-panel");
  const btn   = document.getElementById("ask-page-btn");

  panel.classList.remove("open");
  document.body.classList.remove("chainlit-panel-open");
  btn.classList.remove("open");
  panelOpen = false;
}

function togglePanel() {
  panelOpen ? closePanel() : void openPanel();
}

// ─── 6. Boot ──────────────────────────────────────────────────────
applyPanelWidth();
createFloatingButton();
createPanel();
