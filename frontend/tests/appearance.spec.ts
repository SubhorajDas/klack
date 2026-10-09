import { test, expect, type Page } from '@playwright/test';

async function appearance(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByRole('button', { name: 'Account settings', exact: true }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
}

test('all seven themes update the app and survive reload and navigation', async ({ page }) => {
  await appearance(page);
  await expect(page.getByRole('radio')).toHaveCount(7);
  const palettes = new Set<string>();
  for (const name of ['Light', 'Dark', 'Midnight', 'Ocean', 'Forest', 'Rose', 'Sand']) {
    await page.getByRole('radio', { name: new RegExp(`^${name} `) }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', name.toLowerCase());
    await expect(page.getByRole('status')).toHaveText(`${name} theme selected`);
    const colors = await page.locator('.app-main').evaluate((element) => {
      const style = getComputedStyle(element);
      return `${style.backgroundColor}/${style.color}`;
    });
    palettes.add(colors);
    await page.mouse.move(0, 0);
    await page.screenshot({
      path: test.info().outputPath(`${name.toLowerCase()}.png`),
      animations: 'disabled',
    });
  }
  expect(palettes.size).toBe(7);
  await page.getByRole('radio', { name: /^Dark / }).check();
  await expect(page.locator('.settings-layout > nav button.selected')).toHaveCSS(
    'background-color',
    'rgb(57, 48, 79)',
  );
  await page.getByLabel('Compact messages').check();
  await page.reload();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
  await expect(page.getByRole('radio', { name: /^Dark / })).toBeChecked();
  await expect(page.getByLabel('Compact messages')).toBeChecked();
  await expect(page.locator('html')).toHaveAttribute('data-compact', 'true');
  await page.goto(
    '/w/11111111-1111-4111-8111-111111111111/channel/22222222-2222-4222-8222-222222222222',
  );
  await expect(page.getByRole('log')).toContainText('Here’s the latest iteration');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('.composer')).toHaveCSS('background-color', 'rgb(32, 33, 43)');
  await expect(page.locator('.message-content p').first()).toHaveCSS('color', 'rgb(240, 239, 247)');
  await page.screenshot({ path: test.info().outputPath('dark-conversation.png') });
});

test('saved appearance is applied before hydration and invalid preferences fall back to light', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('klack:theme', 'midnight'));
  await page.route('**/_next/**/*.js*', (route) => route.abort());
  await page.goto('/login');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'midnight');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(16, 28, 48)');
  await page.addInitScript(() => localStorage.setItem('klack:theme', 'unknown'));
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('mobile appearance works when browser storage is unavailable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => {
      throw new Error('Storage unavailable');
    };
    Storage.prototype.setItem = () => {
      throw new Error('Storage unavailable');
    };
  });
  await appearance(page);
  await page.getByRole('radio', { name: /^Forest / }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'forest');
  await expect(page.getByRole('radio', { name: /^Forest / })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: test.info().outputPath('mobile-forest.png') });
});

test('theme changes stay in sync across open tabs', async ({ page, context }) => {
  await appearance(page);
  const second = await context.newPage();
  await second.goto('/settings');
  await second.getByRole('button', { name: 'Appearance', exact: true }).click();
  await page.getByRole('radio', { name: /^Ocean / }).check();
  await expect(second.locator('html')).toHaveAttribute('data-theme', 'ocean');
  await expect(second.getByRole('radio', { name: /^Ocean / })).toBeChecked();
  await second.getByRole('radio', { name: /^Midnight / }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'midnight');
  await expect(page.getByRole('radio', { name: /^Midnight / })).toBeChecked();
});
