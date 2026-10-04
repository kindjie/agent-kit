/* Headless file:// checks. Exit 77 means required tools are unavailable. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
let playwright;
try { playwright = require('playwright'); }
catch (_) { console.log('SKIP: pinned Playwright is not installed');
  process.exit(77); }

const root = process.argv[2];
const tool = path.join(root, 'bin', 'review-sheet');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'review-sheet-test-'));
function run(...args) {
  const result = spawnSync(tool, args, {encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}
function write(name, value) {
  fs.writeFileSync(path.join(temp, name), value);
}
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4' +
  'z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==', 'base64');
const gif = Buffer.from('R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=', 'base64');
const wave = Buffer.alloc(44 + 1600);
wave.write('RIFF', 0); wave.writeUInt32LE(wave.length - 8, 4);
wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16);
wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22);
wave.writeUInt32LE(8000, 24); wave.writeUInt32LE(16000, 28);
wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34);
wave.write('data', 36); wave.writeUInt32LE(1600, 40);
write('one.png', png); write('one.gif', gif);
write('one.wav', wave); write('one.txt', 'Sample <script> text');
write('one.webm', Buffer.from('invalid video'));
write('gone.png', png);
const description = {
  review: 'browser-fixture', title: 'Browser fixture',
  instructions: 'Choose a verdict.', privacy: 'private',
  decisions: {verdict: {kind: 'choice', required: true,
    options: ['keep', 'revise'], keys: 'ab'},
    flaws: {kind: 'flags', options: ['blur', 'noise']},
    note: {kind: 'text'}, score: {kind: 'number', min: 0, max: 5}},
  groups: [{id: 'a', layout: 'grid', pick: {kind: 'best', required: true},
    items: [
      {id: 'still', media: [{kind: 'image', src: 'one.png'}]},
      {id: 'motion', media: [{kind: 'animated', src: 'one.gif'}]},
      {id: 'sound', media: [{kind: 'audio', src: 'one.wav'}]},
      {id: 'words', media: [{kind: 'text', src: 'one.txt'}]},
      {id: 'movie', media: [{kind: 'video', src: 'one.webm'}]},
      {id: 'url', media: [{kind: 'link', src: 'https://example.invalid'}]},
    ]}],
};
write('description.json', JSON.stringify(description));
run('build', path.join(temp, 'description.json'), '--output',
  path.join(temp, 'review.html'));
const url = 'file://' + path.join(temp, 'review.html');
const authority = {review: 'authority-fixture', privacy: 'private',
  decisions: {approve: {kind: 'boolean', authority: true, required: true}},
  items: [{id: 'one', media: [{kind: 'image', src: 'one.png'}]}]};
write('authority.json', JSON.stringify(authority));
run('build', path.join(temp, 'authority.json'), '--output',
  path.join(temp, 'authority.html'));
const originalAuthority = fs.readFileSync(path.join(temp, 'authority.html'),
  'utf8');
const changedPng = Buffer.from(png); changedPng[changedPng.length - 1] ^= 1;
let corrupt = originalAuthority.replace(png.toString('base64'),
  changedPng.toString('base64'));
assert.notEqual(corrupt, originalAuthority);
const inline = corrupt.match(/<script>([\s\S]*?)<\/script>/)[1];
const newHash = crypto.createHash('sha256').update(inline).digest('base64');
corrupt = corrupt.replace(/script-src 'sha256-[^']+'/,
  "script-src 'sha256-" + newHash + "'");
write('authority-corrupt.html', corrupt);
const missing = {review: 'missing-fixture',
  decisions: {verdict: {kind: 'boolean', required: true}},
  items: [{id: 'gone', media: [{kind: 'image', src: 'gone.png'}]}]};
write('missing.json', JSON.stringify(missing));
run('build', path.join(temp, 'missing.json'), '--output',
  path.join(temp, 'missing.html'));
fs.unlinkSync(path.join(temp, 'gone.png'));

async function check(browserType, name) {
  const options = {headless: true};
  if (name === 'Firefox' && process.env.REVIEW_SHEET_FIREFOX_EXECUTABLE)
    options.executablePath = process.env.REVIEW_SHEET_FIREFOX_EXECUTABLE;
  options.timeout = 10000;
  let browser;
  try { browser = await browserType.launch(options); }
  catch (error) {
    const unavailable = new Error(name + ' could not launch: ' +
      String(error.message).split('\n')[0]);
    unavailable.browserUnavailable = true;
    throw unavailable;
  }
  const context = await browser.newContext({acceptDownloads: true});
  const page = await context.newPage();
  const errors = [];
  const remoteRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().startsWith('https://'))
      remoteRequests.push(request.url());
  });
  await page.goto(url);
  await page.locator('.item').first().waitFor();
  assert.equal(await page.locator('.item').count(), 6);
  for (const selector of ['img', 'audio', 'video', 'pre', 'code'])
    assert.ok(await page.locator('.media ' + selector).count() > 0,
      name + ': missing ' + selector);
  assert.equal(await page.getByText('Sample <script> text').count(), 1);
  assert.equal(await page.getByRole('button', {name: 'Copy link'}).count(), 0);
  await page.keyboard.press('a');
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'answered');
  await page.keyboard.press('b');
  await page.keyboard.press('u');
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'answered');
  await page.keyboard.press('1');
  assert.equal(await page.locator('[data-scope-state="item:still:flaws"]')
    .textContent(), 'answered');
  await page.keyboard.press('u');
  await page.reload();
  assert.equal(await page.locator('[data-scope-state="item:still:flaws"]')
    .textContent(), 'unanswered');
  await page.keyboard.press('1');
  await page.reload();
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'answered');
  await page.getByRole('textbox', {name: 'Reviewer (optional metadata)'})
    .fill('Reviewer');
  await page.reload();
  assert.equal(await page.locator('#reviewer').inputValue(), 'Reviewer');
  await page.keyboard.press('j');
  assert.equal(await page.locator('.item.focused').getAttribute('data-item-id'),
    'motion');
  await page.keyboard.press('n');
  assert.equal(await page.locator('.item.focused').getAttribute('data-item-id'),
    'sound');
  await page.keyboard.press('?');
  assert.ok(await page.getByText('Keyboard controls').count());
  await page.getByRole('button', {name: 'Close'}).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export results'}).click();
  const download = await downloadPromise;
  const packet = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
  assert.equal(packet.answers['item:still:verdict'].value, 'keep');
  assert.equal(packet.answers['item:motion:verdict'].state, 'unanswered');
  const stored = await page.evaluate(() => localStorage.getItem(
    'review-sheet:browser-fixture'));
  await page.evaluate(() => localStorage.setItem('review-sheet:browser-fixture',
    '{broken'));
  await page.reload();
  assert.match(await page.locator('#messages').textContent(),
    /Stored draft is unreadable/);
  await page.evaluate(raw => localStorage.setItem(
    'review-sheet:browser-fixture', raw), stored);
  description.instructions = 'Changed instructions.';
  write('description.json', JSON.stringify(description));
  run('build', path.join(temp, 'description.json'), '--output',
    path.join(temp, 'review.html'));
  await page.reload();
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'stale');
  description.instructions = 'Choose a verdict.';
  write('description.json', JSON.stringify(description));
  run('build', path.join(temp, 'description.json'), '--output',
    path.join(temp, 'review.html'));
  await page.reload();
  const incoming = {...packet, answers: {...packet.answers,
    'item:still:verdict': {...packet.answers['item:still:verdict'],
      value: 'revise'}}};
  await page.locator('#import-file').setInputFiles({name: 'results.json',
    mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(incoming))});
  await page.getByText('Choose each conflicting answer').waitFor();
  assert.ok(await page.getByText('Choose each conflicting answer').count());
  assert.ok(await page.getByRole('button', {name: /Keep draft: "keep"/})
    .count());
  await page.getByRole('button', {name: 'Close'}).click();
  const authorityPage = await context.newPage();
  await authorityPage.goto('file://' + path.join(temp, 'authority.html'));
  await authorityPage.getByRole('button', {name: 'true'}).click();
  assert.equal(await authorityPage.locator('[data-scope-state="item:one:approve"]')
    .textContent(), 'unanswered');
  await authorityPage.getByRole('button', {name: /Confirm true/}).click();
  assert.equal(await authorityPage.locator('[data-scope-state="item:one:approve"]')
    .textContent(), 'answered');
  const authorityDownload = authorityPage.waitForEvent('download');
  await authorityPage.getByRole('button', {name: 'Export results'}).click();
  const authorityPacket = JSON.parse(fs.readFileSync(
    await (await authorityDownload).path(), 'utf8'));
  assert.equal(authorityPacket.answers['item:one:approve'].media_hashes[
    'one.png'], crypto.createHash('sha256').update(png).digest('hex'));
  await authorityPage.evaluate(() => localStorage.removeItem(
    'review-sheet:authority-fixture'));
  const corruptedPage = await context.newPage();
  await corruptedPage.goto('file://' + path.join(temp,
    'authority-corrupt.html'));
  assert.ok(await corruptedPage.getByText(/verification failed/).count());
  await corruptedPage.getByRole('button', {name: 'true'}).click();
  await corruptedPage.getByRole('button', {name: /Confirm true/}).click();
  assert.equal(await corruptedPage.locator(
    '[data-scope-state="item:one:approve"]').textContent(), 'invalid');
  const missingPage = await context.newPage();
  await missingPage.goto('file://' + path.join(temp, 'missing.html'));
  await missingPage.getByText('Media could not load. Decisions disabled.')
    .waitFor();
  await missingPage.getByRole('button', {name: 'true'}).click();
  assert.equal(await missingPage.locator(
    '[data-scope-state="item:gone:verdict"]').textContent(), 'invalid');
  assert.ok(!errors.length, errors.join('\n'));
  const blocked = await page.evaluate(() => fetch('https://example.invalid/')
    .then(() => false, () => true));
  assert.equal(blocked, true, name + ': CSP allowed remote fetch');
  assert.deepEqual(remoteRequests, [], name + ': attempted remote request');
  await browser.close();
  console.log('PASS: ' + name + ' file:// page and components');
}

(async () => {
  const missing = [];
  for (const name of ['chromium', 'firefox']) {
    const executable = name === 'firefox' &&
      process.env.REVIEW_SHEET_FIREFOX_EXECUTABLE ||
      playwright[name].executablePath();
    if (!fs.existsSync(executable)) missing.push(name);
  }
  if (missing.length) {
    console.log('SKIP: Playwright browsers absent: ' + missing.join(', '));
    process.exit(77);
  }
  try {
    await check(playwright.chromium, 'Chromium');
    await check(playwright.firefox, 'Firefox');
  } catch (error) {
    if (error.browserUnavailable) {
      console.log('SKIP: ' + error.message);
      process.exitCode = 77;
      return;
    }
    throw error;
  } finally {
    fs.rmSync(temp, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exit(1); });
