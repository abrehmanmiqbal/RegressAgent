import { existsSync } from "node:fs";
import axe from "axe-core";
import { chromium } from "playwright";

const base = process.env.UI_BASE;
const token = process.env.UI_TOKEN;
const candidates = [
  process.env.REGRESSAGENT_CHROMIUM_PATH,
  process.env.CHROMIUM_PATH,
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
  chromium.executablePath(),
].filter(Boolean);
const executablePath = candidates.find((candidate) => existsSync(candidate));
if (!base || !token) throw new Error("UI_BASE and UI_TOKEN are required");
if (!executablePath) throw new Error("No Chromium executable found; install it with `npx playwright install chromium`");

const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  reducedMotion: "reduce",
});
await context.addInitScript({ content: axe.source });
const page = await context.newPage();
let auditCount = 0;

async function audit(label) {
  const result = await page.evaluate(async () => {
    const report = await window.axe.run(document, {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"],
      },
    });
    return {
      checks: report.passes.length,
      violations: report.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        nodes: violation.nodes.slice(0, 5).map((node) => ({
          target: node.target,
          summary: node.failureSummary,
        })),
      })),
    };
  });
  auditCount += 1;
  if (result.violations.length) {
    throw new Error(`${label}: ${JSON.stringify(result.violations, null, 2)}`);
  }
  console.log(`PASS ${label}: ${result.checks} axe checks, 0 violations`);
}

try {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.waitForSelector("#login:not([hidden])");
  await audit("login");

  await page.locator("#token-input").fill(token);
  await page.locator("#token-save").click();
  await page.waitForSelector("#shell:not([hidden])");
  await page.waitForSelector("#onboarding");
  await audit("overview / auto theme");

  await page.locator("#theme-toggle").click();
  await audit("overview / light theme");
  await page.locator("#theme-toggle").click();
  await audit("overview / dark theme");

  await page.locator('#nav a[href="#/cases"]').click();
  await page.waitForSelector("#cases-list");
  await page.locator("#main details > summary").click();
  await page.waitForSelector("#case-new:visible");
  await audit("cases / desktop");

  await page.locator('#nav a[href="#/traces"]').click();
  await page.waitForSelector("#traces-list");
  await page.locator("#main details > summary").click();
  await page.waitForSelector("#trace-new:visible");
  await audit("traces / desktop");

  await page.locator("#palette-open").click();
  await page.waitForSelector('#palette:not([hidden])');
  await audit("command palette dialog");
  await page.keyboard.press("Escape");
  await page.locator("#palette").waitFor({ state: "hidden" });

  await page.setViewportSize({ width: 390, height: 844 });
  await audit("traces / mobile viewport");

  console.log(`Accessibility audit complete: ${auditCount} real-Chromium states; WCAG 2.1 A/AA and axe best-practice rules; zero violations.`);
} finally {
  await browser.close();
}
