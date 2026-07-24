// Baby Buddy - "Last fed" lock-screen widget for the Scriptable iOS app.
//
// Shows the most recent feeding (clock time, amount, and how long ago) in a
// lock-screen accessory widget. Works from a Baby Buddy PWA install because it
// talks to the REST API directly over HTTPS with a token - no native app needed.
//
// Setup: see README.md in this folder. In short:
//   1. Install "Scriptable" from the App Store.
//   2. Paste this file into a new Scriptable script named "BabyBuddyLastFed".
//   3. Set BASE_URL below, and provide your API key (see API_TOKEN note).
//   4. Long-press the lock screen -> Customize -> add a widget -> Scriptable ->
//      pick this script.
//
// SECURITY: Do NOT commit your real API key. Prefer passing it through the
// widget's "Parameter" field (Widget Parameter) or Scriptable's Keychain rather
// than hardcoding it here.

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// Your Baby Buddy base URL, no trailing slash. e.g. "https://baby.example.com"
const BASE_URL = "https://YOUR-BABYBUDDY-URL";

// API token resolution order:
//   1. The widget "Parameter" field (recommended - set it when adding the widget).
//   2. Scriptable Keychain under the key below (run once in-app to store it).
//   3. The API_TOKEN constant (least secure; avoid committing a real value).
const KEYCHAIN_KEY = "babybuddy_api_token";
const API_TOKEN = ""; // optional fallback - leave blank if using Parameter/Keychain

// Optional: restrict to a single child by id (from /api/children/). Leave null
// to just show the latest feeding across all children.
const CHILD_ID = null;

// ---------------------------------------------------------------------------
// Token resolution
// ---------------------------------------------------------------------------

function resolveToken() {
  if (args.widgetParameter && String(args.widgetParameter).trim()) {
    return String(args.widgetParameter).trim();
  }
  if (typeof Keychain !== "undefined" && Keychain.contains(KEYCHAIN_KEY)) {
    return Keychain.get(KEYCHAIN_KEY);
  }
  return API_TOKEN;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function fetchLatestFeeding(token) {
  let url = `${BASE_URL}/api/feedings/?limit=1&ordering=-start`;
  if (CHILD_ID != null) url += `&child=${encodeURIComponent(CHILD_ID)}`;

  const req = new Request(url);
  req.headers = { Authorization: `Token ${token}`, Accept: "application/json" };
  req.timeoutInterval = 15;

  const json = await req.loadJSON();
  const status = req.response ? req.response.statusCode : 0;
  if (status && status >= 400) {
    throw new Error(`HTTP ${status}`);
  }
  const results = json && json.results ? json.results : [];
  return results.length ? results[0] : null;
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatClock(date) {
  const df = new DateFormatter();
  df.useNoDateStyle();
  df.useShortTimeStyle(); // respects the device's 12h/24h setting
  return df.string(date);
}

// Baby Buddy stores amount as a plain number; display in mL to match the app.
function formatAmount(amount) {
  if (amount == null || amount === "") return null;
  const n = Number(amount);
  if (Number.isNaN(n)) return null;
  const rounded = Math.round(n * 100) / 100; // trim float noise
  return `${rounded}mL`;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function addLine(stack, text, opts = {}) {
  const t = stack.addText(text);
  t.font = opts.font || Font.systemFont(13);
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.7;
  return t;
}

// Adds a live, self-updating relative time. iOS re-renders it on its own between
// widget refreshes, so it stays in sync with the clock (renders as e.g.
// "1 hr, 23 min"). No trailing " ago" — the self-sizing field reserves width, which
// would leave a gap before any static suffix.
function addRelativeTime(stack, date, font) {
  const d = stack.addDate(date);
  d.applyRelativeStyle();
  d.font = font;
  d.lineLimit = 1;
  d.minimumScaleFactor = 0.7;
  return d;
}

function buildWidget(feeding, errorText) {
  const w = new ListWidget();
  w.url = BASE_URL; // tap opens Baby Buddy
  // Refresh hint (~12 min). iOS ultimately controls the real cadence.
  w.refreshAfterDate = new Date(Date.now() + 12 * 60 * 1000);

  const family = config.widgetFamily; // undefined when run in-app preview

  // --- error / empty states ---
  if (errorText) {
    if (family === "accessoryInline") {
      addLine(w, `🍼 ${errorText}`);
    } else {
      addLine(w, "Baby Buddy", { font: Font.mediumSystemFont(12) });
      addLine(w, errorText, { font: Font.systemFont(12) });
    }
    return w;
  }
  if (!feeding) {
    if (family === "accessoryInline") addLine(w, "🍼 No feeds yet");
    else {
      addLine(w, "Last fed", { font: Font.mediumSystemFont(12) });
      addLine(w, "No feeds yet", { font: Font.systemFont(12) });
    }
    return w;
  }

  const start = new Date(feeding.start);
  const clock = formatClock(start);
  const amount = formatAmount(feeding.amount);

  // --- inline: single line beside the clock ---
  if (family === "accessoryInline") {
    const bits = [clock];
    if (amount) bits.push(amount);
    addLine(w, `🍼 ${bits.join(" · ")}`);
    return w;
  }

  // --- circular: minimal, time-since is most glanceable ---
  if (family === "accessoryCircular") {
    const c = w.addStack();
    c.layoutVertically();
    c.addSpacer();
    const row = c.addStack();
    row.addSpacer();
    addLine(row, "🍼", { font: Font.systemFont(12) });
    row.addSpacer();
    const row2 = c.addStack();
    row2.addSpacer();
    addRelativeTime(row2, start, Font.boldSystemFont(12));
    row2.addSpacer();
    c.addSpacer();
    return w;
  }

  // --- rectangular (default lock-screen target) + home-screen fallback ---
  const header = w.addStack();
  header.centerAlignContent();
  const symbol = SFSymbol.named("drop.fill");
  if (symbol) {
    const img = header.addImage(symbol.image);
    img.imageSize = new Size(12, 12);
    header.addSpacer(4);
  }
  addLine(header, `Last fed ${clock}`, { font: Font.mediumSystemFont(13) });

  w.addSpacer(2);
  const line2 = w.addStack();
  line2.centerAlignContent();
  const bodyFont = Font.systemFont(12);
  if (amount) addLine(line2, `${amount} · `, { font: bodyFont });
  addRelativeTime(line2, start, bodyFont);

  return w;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  let widget;

  if (!BASE_URL || BASE_URL.includes("YOUR-BABYBUDDY-URL")) {
    widget = buildWidget(null, "Set BASE_URL");
  } else {
    const token = resolveToken();
    if (!token) {
      widget = buildWidget(null, "Set token");
    } else {
      try {
        const feeding = await fetchLatestFeeding(token);
        widget = buildWidget(feeding, null);
      } catch (e) {
        const msg = String(e && e.message ? e.message : e);
        widget = buildWidget(null, msg.includes("401") ? "Bad token" : "—");
      }
    }
  }

  if (config.runsInWidget) {
    Script.setWidget(widget);
  } else if (typeof widget.presentAccessoryRectangular === "function") {
    // In-app run: preview the lock-screen rectangular family.
    await widget.presentAccessoryRectangular();
  } else {
    await widget.presentSmall();
  }
  Script.complete();
}

await main();
