import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, "..");
const RESULTS_DIR = path.join(FRONTEND_DIR, "test-results");
const BASE_URL = (process.env.ROLLMETRICS_E2E_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");

function assert(cond, msg) {
  if (!cond) {
    throw new Error(`E2E_CI_FAIL: ${msg}`);
  }
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (err) {
    const npxCache = path.join(process.env.HOME || "", ".npm", "_npx");
    if (fs.existsSync(npxCache)) {
      for (const entry of fs.readdirSync(npxCache)) {
        const candidate = path.join(npxCache, entry, "node_modules", "playwright", "index.mjs");
        if (fs.existsSync(candidate)) {
          return await import(candidate);
        }
      }
    }
    throw err;
  }
}

async function assertNoHorizontalOverflow(page, label) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  assert(
    metrics.scrollWidth <= metrics.clientWidth + 2,
    `${label}: horizontal overflow detected (${metrics.scrollWidth}px > ${metrics.clientWidth}px)`,
  );
}

async function saveFailureScreenshot(page, name) {
  if (!page) return;
  try {
    fs.mkdirSync(RESULTS_DIR, { recursive: true });
    const outPath = path.join(RESULTS_DIR, `${name}.png`);
    await page.screenshot({ path: outPath, fullPage: true });
    console.error(`Saved failure screenshot: ${outPath}`);
  } catch {
    // Best-effort artifact capture
  }
}

async function verifyLandingPage(browser, serverIsCloud) {
  const expectedClocks = ["02:34", "02:31", "02:28", "02:25"];

  for (const vp of [
    { name: "desktop", width: 1440, height: 820 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const page = await context.newPage();
    try {
      if (!serverIsCloud) {
        await page.route("**/api/auth/config", async (route) => {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              environment: "cloud",
              google_client_id: "ci-e2e-client",
              allow_local_dev_bypass: false,
              auth_required: true,
              public_url: BASE_URL,
              default_profile: "cloud",
            }),
          });
        });
      }

      await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForSelector('[data-testid="cloud-signin-gate"]', { timeout: 15000 });

      const heroText = (await page.locator(".landing-hero").textContent()) || "";
      assert(heroText.includes("Break down jiu-jitsu matches."), `Landing (${vp.name}): missing hero H1`);
      assert(
        (await page.locator(".landing-eyebrow").count()) === 0,
        `Landing (${vp.name}): .landing-eyebrow should be removed`,
      );
      assert(
        (await page.locator('[data-testid="landing-slowmo-toggle"]').count()) === 0,
        `Landing (${vp.name}): slow-mo toggle should be removed`,
      );
      assert(!heroText.includes("IBJJF v6.1"), `Landing (${vp.name}): IBJJF v6.1 badge should be removed`);

      // Wait for landing preview video to load metadata/frames (readyState >= 2)
      await page.waitForFunction(
        () => {
          const v = document.querySelector('[data-testid="landing-video-preview"] video');
          return Boolean(v && v.readyState >= 2 && v.videoWidth > 0);
        },
        null,
        { timeout: 15000 },
      );

      // Pause video so live timeupdate does not race click assertions
      await page.evaluate(() => {
        const v = document.querySelector('[data-testid="landing-video-preview"] video');
        if (v) v.pause();
      });

      // Timeline strictly above scoreboard
      const tlBox = await page.locator('[data-testid="landing-timeline"]').boundingBox();
      const sbBox = await page.locator('[data-testid="landing-scoreboard"]').boundingBox();
      assert(tlBox && sbBox && tlBox.y < sbBox.y, `Landing (${vp.name}): timeline must be above scoreboard`);

      // All 4 static moment rows visible simultaneously
      for (let i = 0; i < 4; i += 1) {
        assert(
          await page.locator(`[data-testid="landing-moment-${i}"]`).isVisible(),
          `Landing (${vp.name}): landing-moment-${i} not visible`,
        );
      }

      // Real pointer clicks (NO force: true) on every diamond with two-way sync
      for (let i = 0; i < 4; i += 1) {
        const pin = page.locator(`[data-testid="landing-pin-${i}"]`);
        await pin.click();
        assert(
          (await pin.getAttribute("aria-pressed")) === "true",
          `Landing (${vp.name}): landing-pin-${i} not active after click`,
        );
        assert(
          (await page.locator(`[data-testid="landing-moment-${i}"]`).getAttribute("aria-pressed")) === "true",
          `Landing (${vp.name}): landing-moment-${i} did not sync from pin click`,
        );
        const clockText = ((await page.locator('[data-testid="landing-score-clock"]').textContent()) || "").trim();
        assert(
          clockText === expectedClocks[i],
          `Landing (${vp.name}): expected clock ${expectedClocks[i]} on pin ${i}, got ${clockText}`,
        );
      }

      // Real pointer clicks on every moment row with two-way sync back to diamonds
      for (let i = 0; i < 4; i += 1) {
        const row = page.locator(`[data-testid="landing-moment-${i}"]`);
        await row.click();
        assert(
          (await row.getAttribute("aria-pressed")) === "true",
          `Landing (${vp.name}): landing-moment-${i} not active after row click`,
        );
        assert(
          (await page.locator(`[data-testid="landing-pin-${i}"]`).getAttribute("aria-pressed")) === "true",
          `Landing (${vp.name}): landing-pin-${i} did not sync from row click`,
        );
      }

      // Moment 2 shows Caio Terra winning 4-2
      await page.locator('[data-testid="landing-moment-2"]').click();
      const scoreA = ((await page.locator('[data-testid="landing-score-a"]').textContent()) || "").replace(/\s+/g, "");
      const scoreB = ((await page.locator('[data-testid="landing-score-b"]').textContent()) || "").replace(/\s+/g, "");
      assert(scoreA.startsWith("4") && scoreB.startsWith("2"), `Landing (${vp.name}): expected Terra 4 vs Malfacine 2, got ${scoreA} vs ${scoreB}`);

      await assertNoHorizontalOverflow(page, `Landing (${vp.name})`);
    } catch (err) {
      await saveFailureScreenshot(page, `fail_landing_${vp.name}`);
      throw err;
    } finally {
      await context.close();
    }
  }
}

async function verifyAuthenticatedApp(browser, serverIsCloud) {
  const deskCtx = await browser.newContext({ viewport: { width: 1440, height: 820 } });
  const desk = await deskCtx.newPage();

  try {
    await desk.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 20000 });

    if (serverIsCloud) {
      await desk.waitForSelector('[data-testid="demo-sebastian-btn"]', { timeout: 15000 });
      await desk.locator('[data-testid="demo-sebastian-btn"]').click();
      await desk.waitForSelector('[data-testid="overview-home"]', { timeout: 15000 });
    } else {
      await desk.waitForSelector('[data-testid="brand-home-btn"]', { timeout: 15000 });
      const onHome = await desk.locator('[data-testid="overview-home"]').isVisible();
      if (!onHome) {
        await desk.locator('[data-testid="brand-home-btn"]').click();
        await desk.waitForSelector('[data-testid="overview-home"]', { timeout: 15000 });
      }
    }

    // Dashboard checks
    assert(
      (await desk.locator('[data-testid="team-coach-roster"]').count()) === 0,
      "Dashboard: Team Coach Roster should be absent",
    );
    await assertNoHorizontalOverflow(desk, "Dashboard (desktop)");

    // + Add Match modal on desktop
    await desk.locator('[data-testid="topbar-add-match-btn"]').click();
    await desk.waitForSelector('[data-testid="upload-match-modal"]', { timeout: 10000 });
    await assertNoHorizontalOverflow(desk, "+ Add Match modal (desktop)");
    await desk.locator('[data-testid="upload-match-modal"] button:has-text("Close")').click();

    // Navigate via direct URLs + verify goBack() / goForward()
    await desk.goto(`${BASE_URL}/me`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await desk.waitForSelector('[data-testid="athlete-profile-section"]', { timeout: 15000 });
    await assertNoHorizontalOverflow(desk, "/me (desktop)");

    await desk.goto(`${BASE_URL}/curricula`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await desk.waitForSelector('[data-testid="curricula-view"]', { timeout: 15000 });
    await assertNoHorizontalOverflow(desk, "/curricula (desktop)");

    await desk.goto(`${BASE_URL}/rules/ibjjf-v6-1`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await desk.waitForSelector('[data-testid="rulebook-page"]', { timeout: 15000 });

    await desk.goto(`${BASE_URL}/m/malfacine_terra_2019`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await desk.waitForSelector('[data-testid="row-athlete-a"]', { timeout: 15000 });
    const rowAText = (await desk.locator('[data-testid="row-athlete-a"]').textContent()) || "";
    assert(rowAText.includes("Caio Terra"), `Direct /m/malfacine_terra_2019 loaded wrong match: ${rowAText}`);

    // Scoreboard strictly above <video>
    const sbBox = await desk.locator(".scoreboard-strip").boundingBox();
    const vidBox = await desk.locator(".video-frame video").boundingBox();
    assert(sbBox && vidBox && sbBox.y + sbBox.height <= vidBox.y + 4, "Match Player: scoreboard must be above <video>");

    // Removed controls absent
    assert((await desk.locator('[data-testid="add-moment-btn"]').count()) === 0, "Match Player: + Moment should be absent");
    assert((await desk.locator('[data-testid="edit-moment-btn"]').count()) === 0, "Match Player: Edit Moment should be absent");
    assert((await desk.locator('[data-testid="tap-to-identify-toggle"]').count()) === 0, "Match Player: Pin A/B should be absent");

    // Feedback workflow works
    await desk.locator('[data-testid="moment-feedback-btn"]').first().click();
    await desk.waitForSelector('[data-testid="moment-feedback-form"]', { timeout: 5000 });
    await desk.locator('[data-testid="moment-feedback-form"] input').fill("CI E2E feedback check");
    await desk.locator('[data-testid="submit-feedback-btn"]').click();
    await desk.waitForSelector('[data-testid="moment-feedback-saved"]', { timeout: 5000 });

    await assertNoHorizontalOverflow(desk, "Match Player (desktop)");

    // Browser history goBack() / goForward() check
    await desk.goBack({ waitUntil: "domcontentloaded" });
    await desk.waitForSelector('[data-testid="rulebook-page"]', { timeout: 10000 });
    await desk.goForward({ waitUntil: "domcontentloaded" });
    await desk.waitForSelector('[data-testid="row-athlete-a"]', { timeout: 10000 });

    // Mobile (390x844) overflow checks using the same authenticated storage state
    const storageState = await deskCtx.storageState();
    const mobCtx = await browser.newContext({
      viewport: { width: 390, height: 844 },
      storageState,
    });
    const mob = await mobCtx.newPage();
    try {
      await mob.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 15000 });
      await mob.waitForSelector('[data-testid="brand-home-btn"]', { timeout: 15000 });
      if (!(await mob.locator('[data-testid="overview-home"]').isVisible())) {
        await mob.locator('[data-testid="brand-home-btn"]').click();
        await mob.waitForSelector('[data-testid="overview-home"]', { timeout: 15000 });
      }
      await assertNoHorizontalOverflow(mob, "Dashboard (mobile)");

      await mob.locator('[data-testid="topbar-add-match-btn"]').click();
      await mob.waitForSelector('[data-testid="upload-match-modal"]', { timeout: 10000 });
      await assertNoHorizontalOverflow(mob, "+ Add Match modal (mobile)");
      await mob.locator('[data-testid="upload-match-modal"] button:has-text("Close")').click();

      await mob.goto(`${BASE_URL}/me`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await mob.waitForSelector('[data-testid="athlete-profile-section"]', { timeout: 15000 });
      await assertNoHorizontalOverflow(mob, "/me (mobile)");

      await mob.goto(`${BASE_URL}/curricula`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await mob.waitForSelector('[data-testid="curricula-view"]', { timeout: 15000 });
      await assertNoHorizontalOverflow(mob, "/curricula (mobile)");

      await mob.goto(`${BASE_URL}/m/malfacine_terra_2019`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await mob.waitForSelector('[data-testid="row-athlete-a"]', { timeout: 15000 });
      const mobSb = await mob.locator(".scoreboard-strip").boundingBox();
      const mobVid = await mob.locator(".video-frame video").boundingBox();
      assert(mobSb && mobVid && mobSb.y + mobSb.height <= mobVid.y + 4, "Match Player (mobile): scoreboard must be above <video>");
      await assertNoHorizontalOverflow(mob, "Match Player (mobile)");
    } catch (err) {
      await saveFailureScreenshot(mob, "fail_mobile_app");
      throw err;
    } finally {
      await mobCtx.close();
    }
  } catch (err) {
    await saveFailureScreenshot(desk, "fail_desktop_app");
    throw err;
  } finally {
    await deskCtx.close();
  }
}

async function main() {
  const cfgResp = await fetch(`${BASE_URL}/api/auth/config`);
  assert(cfgResp.ok, `/api/auth/config returned HTTP ${cfgResp.status}`);
  const cfg = await cfgResp.json();
  const serverIsCloud = cfg.environment === "cloud" || cfg.auth_required === true;

  const { chromium } = await loadPlaywright();
  const launchOpts = { headless: true };
  if (process.env.CHROME_PATH) {
    launchOpts.executablePath = process.env.CHROME_PATH;
  } else if (fs.existsSync("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")) {
    launchOpts.executablePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }

  const browser = await chromium.launch(launchOpts);
  try {
    await verifyLandingPage(browser, serverIsCloud);
    await verifyAuthenticatedApp(browser, serverIsCloud);
    console.log(
      `E2E_CI_PASS: Landing (4 diamonds + 4 rows two-way sync), Demo/Routes (/me, /curricula, /rules/ibjjf-v6-1, /m/malfacine_terra_2019), Match Player, and Desktop+Mobile zero-overflow verified against ${BASE_URL}.`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
