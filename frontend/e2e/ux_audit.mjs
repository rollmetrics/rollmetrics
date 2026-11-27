import path from "node:path";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:8090";
const SHOT_DIR = process.env.SCREENSHOT_DIR || "";

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    return await import(
      "/Users/korymath/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs"
    );
  }
}

function assert(cond, msg) {
  if (!cond) {
    throw new Error(`UX_AUDIT_FAIL: ${msg}`);
  }
}

async function main() {
  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });

  try {
    // 1. DESKTOP 1440x820 FULL WORKFLOW AUDIT
    const desk = await browser.newPage({
      viewport: { width: 1440, height: 820 },
      deviceScaleFactor: 2,
    });
    await desk.goto(BASE_URL, { waitUntil: "networkidle" });
    await desk.waitForSelector('[data-testid="row-athlete-a"]');

    // Verify poster endpoint returns 200 OK image/jpeg
    const posterResp = await desk.request.get(`${BASE_URL}/api/matches/mc9_semifinal/poster`);
    assert(posterResp.status() === 200, `Poster expected 200, got ${posterResp.status()}`);
    assert(
      (posterResp.headers()["content-type"] || "").includes("image/jpeg"),
      "Poster must be image/jpeg",
    );

    // Verify desktop 100dvh zero-scroll fit
    const deskFit = await desk.evaluate(() => ({
      innerH: window.innerHeight,
      scrollH: document.documentElement.scrollHeight,
      innerW: window.innerWidth,
      scrollW: document.documentElement.scrollWidth,
    }));
    assert(deskFit.scrollH <= deskFit.innerH, `Desktop vertical overflow: ${JSON.stringify(deskFit)}`);
    assert(deskFit.scrollW <= deskFit.innerW, `Desktop horizontal overflow: ${JSON.stringify(deskFit)}`);

    // Verify real video play/pause, readyState >= 2, playback advancement > 0.3s, & scrubbing (#131)
    await desk.locator('[aria-label="Play video"]').click();
    await desk.waitForTimeout(1200);
    const playCheck = await desk.evaluate(() => {
      const v = document.querySelector("video");
      return {
        paused: v?.paused,
        currentTime: v?.currentTime || 0,
        readyState: v?.readyState || 0,
      };
    });
    assert(playCheck.paused === false, `Expected desktop video playing, got ${JSON.stringify(playCheck)}`);
    assert(playCheck.currentTime > 0.3, `Expected desktop currentTime > 0.3s, got ${playCheck.currentTime}`);
    assert(playCheck.readyState >= 2, `Expected desktop readyState >= 2, got ${playCheck.readyState}`);
    await desk.locator('[aria-label="Pause video"]').click();

    // Scrub to 01:07 (call #2) and 02:50 (call #3)
    await desk.locator('[data-testid="call-row-2"] button.event-line').click();
    await desk.waitForTimeout(250);
    const t2 = await desk.evaluate(() => document.querySelector("video")?.currentTime || 0);
    assert(Math.abs(t2 - 67) < 2, `Expected seek to ~67s, got ${t2}`);
    await desk.locator('[data-testid="call-row-3"] button.event-line').click();
    await desk.waitForTimeout(250);
    const t3 = await desk.evaluate(() => document.querySelector("video")?.currentTime || 0);
    assert(Math.abs(t3 - 170) < 2, `Expected seek to ~170s, got ${t3}`);

    // Mute / Unmute toggle
    await desk.locator('[aria-label="Mute video"]').click();
    assert(
      (await desk.locator('[aria-label="Unmute video"]').count()) === 1,
      "Mute toggle did not switch to Unmute",
    );
    await desk.locator('[aria-label="Unmute video"]').click();

    // 1-tap Prev Match Moment Stepper (#3 -> #2 +1 PEN) + 4-step Penalty Ladder + edge-clamped hover tooltip
    await desk.locator('[aria-label="Previous match moment"]').click();
    await desk.waitForTimeout(250);
    const ladderText = await desk.locator('[data-testid="penalty-ladder"]').textContent();
    assert((ladderText || "").includes("1 Warning"), "Penalty ladder missing on call #2");
    await desk.locator('[data-testid="pin-2"]').hover();
    await desk.waitForTimeout(150);
    const tipRect = await desk.evaluate(() => {
      const tip = document.querySelector('[role="tooltip"]');
      if (!tip) return null;
      const r = tip.getBoundingClientRect();
      return { left: Math.round(r.left), right: Math.round(r.right), w: window.innerWidth };
    });
    assert(
      tipRect !== null && tipRect.left >= 0 && tipRect.right <= tipRect.w,
      `Tooltip clipped on desktop: ${JSON.stringify(tipRect)}`,
    );

    // Switch through all 6 matches in dropdown
    const optionValues = await desk
      .locator('select[aria-label="Match Selector"] option')
      .evaluateAll((opts) => opts.map((o) => o.value));
    assert(optionValues.length >= 6, `Expected >=6 showcase matches, got ${optionValues.length}`);
    for (const mid of optionValues) {
      await desk.selectOption('select[aria-label="Match Selector"]', mid);
      await desk.waitForTimeout(120);
    }
    await desk.selectOption('select[aria-label="Match Selector"]', "mc9_semifinal");
    await desk.waitForTimeout(200);

    // Match Coaching Summary toggle + CTA links
    await desk.locator('[data-testid="moments-coach-toggle"]').click();
    const coachText = await desk.locator('[data-testid="coach-summary-panel"]').textContent();
    assert((coachText || "").includes("Overview"), "Coach tab missing Overview");
    assert((coachText || "").includes("Kory Mathewson"), "Coach tab missing Athlete A");
    assert((coachText || "").includes("Michael Dube"), "Coach tab missing Athlete B");
    assert((coachText || "").includes("CTA Curriculum ·"), "Coach tab missing CTA Curriculum link");
    if (SHOT_DIR) {
      await desk.screenshot({
        path: path.join(SHOT_DIR, "23_desktop_coach_and_2row_transport.png"),
        fullPage: false,
      });
    }
    await desk.locator('[data-testid="moments-coach-toggle"]').click();

    // 0.4x Highlight inline loop + clickable IBJJF Rule Peek (#145, #146)
    await desk.locator('[data-testid="call-row-2"] button.event-line').click();
    assert(
      (await desk.locator('[data-testid="call-row-2"] button', { hasText: "3-Panel" }).count()) === 0,
      "3-Panel button must be removed from Match Calls UI",
    );
    await desk.locator('[data-testid="call-row-2"] [data-testid="rule-cite-btn"]').click();
    await desk.waitForSelector('[data-testid="rule-peek-box"]');
    await desk.waitForSelector('[data-testid="rulebook-link"]');
    await desk.locator('[data-testid="call-row-2"] [data-testid="rule-cite-btn"]').click();
    await desk.locator('[data-testid="call-row-2"] [data-testid="cta-peek-btn"]').click();
    await desk.waitForSelector('[data-testid="cta-peek-box"]');
    await desk.locator('[data-testid="call-row-2"] [data-testid="cta-peek-btn"]').click();
    await desk.locator('[data-testid="call-row-2"] [data-testid="highlight-btn"]').click();
    await desk.waitForSelector('[data-testid="highlight-video"]', { timeout: 25000 });
    await desk.waitForSelector('[data-testid="highlight-download-link"]');
    await desk.locator('[data-testid="call-row-2"] [data-testid="highlight-btn"]').click();

    // Connection & Model Config modal + Test Local Connection
    await desk.locator('[data-testid="user-profile"]').click();
    await desk.waitForSelector('[aria-label="Connection and Model Config"]');
    await desk.getByRole("button", { name: "Test Local Connection" }).click();
    await desk.waitForSelector(".test-badge", { timeout: 15000 });
    await desk.locator('.config-modal button', { hasText: "Close" }).click();

    // Top-left logo -> Overview Home -> Film Study athlete filter + scouting card -> click match card back (#117, #144)
    await desk.locator('[data-testid="brand-home-btn"]').click();
    await desk.waitForSelector('[data-testid="overview-home"]');
    if (SHOT_DIR) {
      await desk.screenshot({
        path: path.join(SHOT_DIR, "26_overview_home_desktop.png"),
        fullPage: false,
      });
    }
    await desk.locator('[data-testid="film-study-filter-sebastian-bussieres"]').click();
    await desk.waitForSelector('[data-testid="film-study-scouting-card"]');
    const scoutText = await desk.locator('[data-testid="film-study-scouting-card"]').textContent();
    assert((scoutText || "").includes("38-0 pts"), `Expected 38-0 pts in scouting card, got ${scoutText}`);
    if (SHOT_DIR) {
      await desk.locator('[data-testid="home-film-study"]').scrollIntoViewIfNeeded();
      await desk.waitForTimeout(150);
      await desk.screenshot({
        path: path.join(SHOT_DIR, "28_film_study_scouting_bussieres.png"),
        fullPage: false,
      });
    }
    await desk.locator('[data-testid="film-study-filter-all"]').click();
    await desk.locator('[data-testid="home-match-mc9_semifinal"]').click();
    await desk.waitForSelector('[data-testid="row-athlete-a"]');

    // Dedicated App Admin (/admin) + ML Admin (/ml) Dashboards via Settings modal footer
    await desk.locator('[data-testid="user-profile"]').click();
    await desk.waitForSelector('[data-testid="open-admin-btn"]');
    await desk.locator('[data-testid="open-admin-btn"]').click();
    await desk.waitForSelector('[data-testid="admin-dashboard"]');
    await desk.waitForSelector('[data-testid="admin-users-table"]');
    await desk.waitForSelector('[data-testid="admin-jobs-table"]');
    const adminText = await desk.locator('[data-testid="admin-dashboard"]').textContent();
    assert((adminText || "").includes("@"), "Admin dashboard missing user email");
    await desk.locator('[data-testid="switch-ml-admin-btn"]').click();
    await desk.waitForSelector('[data-testid="ml-admin-dashboard"]');
    await desk.waitForSelector('[data-testid="admin-scoreboard-table"] tbody tr td.mono-cell');
    const mlText = await desk.locator('[data-testid="ml-admin-dashboard"]').textContent();
    assert((mlText || "").includes("122 matches"), "ML Admin dashboard missing 122 matches");
    if (SHOT_DIR) {
      await desk.screenshot({
        path: path.join(SHOT_DIR, "29_admin_ml_dashboard.png"),
        fullPage: false,
      });
    }
    await desk.locator('[data-testid="admin-split-calibration"]').click();
    await desk.locator('[data-testid="ml-admin-back-btn"]').click();
    await desk.waitForSelector('[data-testid="row-athlete-a"]');

    // Public /privacy, /terms, and /share/{token} routes (#147, #170)
    await desk.goto(`${BASE_URL}/privacy`, { waitUntil: "networkidle" });
    await desk.waitForSelector('[data-testid="privacy-page"]');
    await desk.goto(`${BASE_URL}/terms`, { waitUntil: "networkidle" });
    await desk.waitForSelector('[data-testid="terms-page"]');
    const shareResp = await desk.request.post(`${BASE_URL}/api/matches/mc9_semifinal/share`);
    assert(shareResp.status() === 200, `Share create failed: ${shareResp.status()}`);
    const shareData = await shareResp.json();
    await desk.goto(`${BASE_URL}${shareData.share_path}`, { waitUntil: "networkidle" });
    await desk.waitForSelector('[data-testid="shared-breakdown-banner"]');
    await desk.waitForSelector('[data-testid="row-athlete-a"]');
    await desk.goto(BASE_URL, { waitUntil: "networkidle" });
    await desk.waitForSelector('[data-testid="row-athlete-a"]');

    // Unified + Add Match modal -> Live Camera tab -> Simulate Live from Match Video -> Stop Live
    await desk.locator('[aria-label="Analyze or Live Camera"]').click();
    await desk.getByRole("tab", { name: "Live Camera" }).click();
    await desk.getByRole("button", { name: "Simulate Live from Match Video" }).click();
    await desk.waitForSelector('[data-testid="live-sim-badge"]');
    await desk.waitForSelector('[data-testid="live-telemetry-strip"]');
    await desk.locator('[data-testid="stop-live-btn"]').click();
    assert((await desk.locator('[data-testid="live-sim-badge"]').count()) === 0, "Stop Live failed");
    await desk.close();

    // 2. MOBILE 390x844 FULL WORKFLOW & ZERO-OVERFLOW AUDIT
    const mob = await browser.newPage({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
    });
    await mob.goto(BASE_URL, { waitUntil: "networkidle" });
    await mob.waitForSelector('[data-testid="row-athlete-a"]');

    const assertNoMobOverflow = async (label) => {
      const sw = await mob.evaluate(() => document.documentElement.scrollWidth);
      assert(sw <= 390, `Mobile horizontal overflow on ${label}: scrollWidth=${sw}`);
    };

    await assertNoMobOverflow("main");
    const compactDivVisible = await mob.evaluate(
      () => getComputedStyle(document.querySelector(".division-short")).display !== "none",
    );
    assert(compactDivVisible, "Compact mobile division chip should be visible at 390px");

    // Verify real mobile video playback (>0.3s, readyState >= 2) + seek (#131)
    await mob.locator('[aria-label="Start playback"]').click();
    await mob.waitForTimeout(1200);
    const mobPlay = await mob.evaluate(() => {
      const v = document.querySelector("video");
      return {
        paused: v?.paused,
        currentTime: v?.currentTime || 0,
        readyState: v?.readyState || 0,
      };
    });
    assert(mobPlay.paused === false, `Expected mobile video playing, got ${JSON.stringify(mobPlay)}`);
    assert(mobPlay.currentTime > 0.3, `Expected mobile currentTime > 0.3s, got ${mobPlay.currentTime}`);
    assert(mobPlay.readyState >= 2, `Expected mobile readyState >= 2, got ${mobPlay.readyState}`);
    await mob.locator('[aria-label="Pause video"]').click();

    // Stepper + Coach tab + screenshot on 390x844
    await mob.locator('[aria-label="Previous match moment"]').click();
    await assertNoMobOverflow("after-stepper");
    if (SHOT_DIR) {
      await mob.screenshot({
        path: path.join(SHOT_DIR, "24_mobile_390x844_2row_poster.png"),
        fullPage: false,
      });
    }

    await mob.locator('[data-testid="moments-coach-toggle"]').click();
    await mob.waitForSelector('[data-testid="coach-summary-panel"]');
    await assertNoMobOverflow("coach-tab");
    if (SHOT_DIR) {
      await mob.screenshot({
        path: path.join(SHOT_DIR, "25_mobile_390x844_coach_summary.png"),
        fullPage: false,
      });
    }
    await mob.locator('[data-testid="moments-coach-toggle"]').click();

    // Mobile + Add Match modal + Settings modal + inline highlight
    await mob.locator('[aria-label="Analyze or Live Camera"]').click();
    await assertNoMobOverflow("add-match-modal");
    await mob.locator('[data-testid="upload-match-modal"] button', { hasText: "Close" }).click();

    await mob.locator('[data-testid="user-profile"]').click();
    await assertNoMobOverflow("settings-modal");
    await mob.locator(".config-modal button", { hasText: "Close" }).click();

    await mob.locator('[data-testid="call-row-2"] button.event-line').click();
    const gifHiddenOnMob = await mob.evaluate(() => {
      const btn = document.querySelector('[data-testid="call-row-2"] [data-testid="highlight-btn"]');
      return !btn || getComputedStyle(btn).display === "none";
    });
    assert(gifHiddenOnMob, "GIF button must be hidden on mobile (<= 760px)");

    // Mobile Overview Home via top-left logo (#117)
    await mob.locator('[data-testid="brand-home-btn"]').click();
    await mob.waitForSelector('[data-testid="overview-home"]');
    await assertNoMobOverflow("overview-home");
    if (SHOT_DIR) {
      await mob.screenshot({
        path: path.join(SHOT_DIR, "27_overview_home_mobile.png"),
        fullPage: false,
      });
    }
    await mob.locator('[data-testid="home-match-mc9_semifinal"]').click();
    await mob.waitForSelector('[data-testid="row-athlete-a"]');
    await mob.close();

    console.log("UX_AUDIT_PASS: Desktop (1440x820) + Mobile (390x844) all interactive workflows verified.");
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
