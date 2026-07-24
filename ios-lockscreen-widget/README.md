# Baby Buddy "Last fed" lock-screen widget (iOS)

A small lock-screen widget that shows your child's most recent feeding — clock
time, amount, and how long ago — pulled live from your Baby Buddy API.

It works even though Baby Buddy is installed as a **web app (PWA)**, because it
uses the free [**Scriptable**](https://scriptable.app/) app to call the REST API
directly. No native app, no App Store build, no server changes.

## What this can and can't be

Read this first so the result matches expectations:

- ✅ **This widget** lives in the small **accessory widget slots directly under
  the lock-screen clock**. It shows live text like `Last fed 17:36` / `30mL · 4h45m`.
- ❌ **The big full-width card** (colored, with an always-on `+` button, sitting
  next to "Now Playing") is an iOS **Live Activity**. Live Activities can only be
  created by a **native app** using ActivityKit. A PWA fundamentally cannot make
  one, so that exact card is not achievable from Baby Buddy on iOS. This is an
  Apple platform limit, not a Baby Buddy limit.

So: you get a compact, tinted, glanceable strip on the lock screen — not the
large colored card. Tapping it opens Baby Buddy.

## Setup

1. **Install Scriptable** from the App Store.
2. **Get your API key:** in Baby Buddy, open **User → Settings** and copy (or
   regenerate) your **API key**.
3. **Add the script:** open Scriptable → `+` (new script) → paste the contents of
   [`BabyBuddyLastFed.js`](BabyBuddyLastFed.js). Rename it to `BabyBuddyLastFed`.
4. **Set your URL:** edit the `BASE_URL` constant near the top to your Baby Buddy
   address, with no trailing slash, e.g. `https://baby.example.com`.
5. **Provide the token** (pick one — the widget Parameter is recommended and keeps
   the key out of the script body):
   - **Widget Parameter (recommended):** leave `API_TOKEN` blank; you'll paste the
     key into the widget's *Parameter* field in step 7.
   - **Keychain:** run this once in Scriptable to store it, then delete the line:
     `Keychain.set("babybuddy_api_token", "YOUR_KEY")`.
   - **Inline (least secure):** set `API_TOKEN = "YOUR_KEY"` in the script. Don't
     do this if you'll share the script.
6. **Preview:** tap ▶ in Scriptable. It renders the rectangular layout. If you see
   `Set BASE_URL`, `Set token`, or `Bad token`, fix that config and re-run.
7. **Add to the lock screen:** lock the phone → long-press the lock screen →
   **Customize** → tap the widget area under the clock → **add a widget** →
   **Scriptable** → choose a rectangular size → select the **BabyBuddyLastFed**
   script. If using the Parameter method, tap the widget and paste your API key
   into **Parameter**. Done.

## Optional tweaks

- **One child only:** set `CHILD_ID` to a child id (from `/api/children/`) to pin
  the widget to a specific child.
- **Tap target:** the widget opens `BASE_URL` on tap. To instead run a shortcut
  (e.g. your voice-logging shortcut), change `w.url` in the script to a
  `shortcuts://run-shortcut?name=...` URL.

## Verify the API from a computer

Confirms your key and URL work, and shows the exact JSON the widget reads:

```bash
curl -H "Authorization: Token YOUR_API_KEY" \
  "https://YOUR-BABYBUDDY-URL/api/feedings/?limit=1&ordering=-start"
```

You should get `{"count": …, "results": [{"start": …, "amount": …, …}]}`.

## Limitations

- Lock-screen widgets are **small and tinted/monochrome** — no color, avatar, or
  large card layout.
- **iOS controls the refresh cadence** (typically several minutes to ~15 min); the
  "time ago" value can lag and isn't real-time.
- A widget is a **single tap target** — it can show data *or* act as one button,
  not both. An inline `+` button alongside live data requires a native Live
  Activity (see above).
