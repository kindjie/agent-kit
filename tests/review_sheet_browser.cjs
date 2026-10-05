/* Headless file:// checks. Exit 77 means required tools are unavailable. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');
let playwright;
try { playwright = require('playwright'); }
catch (_) { console.log('SKIP: pinned Playwright is not installed');
  process.exit(77); }

const root = process.argv[2];
const tool = path.join(root, 'bin', 'review-sheet');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'review-sheet-test-'));
process.on('exit', () => fs.rmSync(temp, {recursive: true, force: true}));
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

const generatedImages = new Map();
function generatedPng(width, height, seed) {
  const key = [width, height, seed].join(':');
  if (generatedImages.has(key)) return generatedImages.get(key);
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++)
        crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }
  function chunk(type, data) {
    const name = Buffer.from(type);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([name, data])));
    return Buffer.concat([length, name, data, crc]);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 6;
  const rows = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const offset = y * (width * 4 + 1) + 1 + x * 4;
    rows[offset] = (Math.floor(x / 8) + seed) % 256;
    rows[offset + 1] = (Math.floor(y / 8) + seed) % 256;
    rows[offset + 2] = seed % 256; rows[offset + 3] = 255;
  }
  const result = Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'),
    chunk('IHDR', header), chunk('IDAT', zlib.deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0))]);
  generatedImages.set(key, result);
  return result;
}

function comparisonCase(number, motion = 2, width = 1, height = 1) {
  const names = ['ref', 'a', 'b', 'c'].map(name => name + number);
  const items = names.map(itemId => ({id: itemId, media:
    ['front', 'side'].map(view => {
      const folder = path.join(temp, 'frames', itemId, view);
      fs.mkdirSync(folder, {recursive: true});
      const hashes = [];
      for (let index = 0; index < motion; index++) {
        const image = width === 1 ? png : generatedPng(width, height, index);
        fs.writeFileSync(path.join(folder, index + '.png'), image);
        hashes.push(crypto.createHash('sha256').update(image).digest('hex'));
      }
      const frames = Array.from({length: motion + 2}, (_, index) => {
        const source = Math.max(0, Math.min(motion - 1, index - 1));
        return {index, source_frame: source, src: source + '.png',
          phase: index === 0 ? 'start_hold' : index === motion + 1 ?
            'end_hold' : 'motion', sha256: hashes[source]};
      });
      fs.writeFileSync(path.join(folder, 'index.json'), JSON.stringify({
        width, height, frames}));
      return {kind: 'frame-sequence', view,
        src: path.relative(temp, path.join(folder, 'index.json'))};
    })}));
  return {id: 'case-' + number, title: 'Case ' + number,
    layout: 'synchronized-comparison',
    pick: {kind: 'best', required: false},
    decisions: {flags: {kind: 'flags',
      options: ['popping', 'slide', 'swing', 'timing']},
      notes: {kind: 'text'}}, items,
    comparison: {reference: names[0], candidates: names.slice(1),
      views: ['front', 'side'], fps: [60, 1], motion_frames: motion,
      hold_start: 1, hold_end: 1, frame_count: motion + 2,
      key_frames: [0, motion - 1], crops: {full: [0, 0, width, height],
        detail: [0, Math.floor(height / 2), width, Math.ceil(height / 2)]},
      reveal: {
          [names[1]]: 'Variant one', [names[2]]: 'Variant two',
          [names[3]]: 'Variant three'}}};
}
const comparison = {review: 'comparison-fixture',
  title: 'Generic comparison', groups: [comparisonCase(1),
    comparisonCase(2)]};
write('comparison.json', JSON.stringify(comparison));
run('build', path.join(temp, 'comparison.json'), '--output',
  path.join(temp, 'comparison.html'));

const config = path.join(temp, 'config');
const componentDir = path.join(temp, 'custom-component');
fs.mkdirSync(componentDir);
fs.writeFileSync(path.join(componentDir, 'component.json'), JSON.stringify({
  kind: 'custom', version: '1.0.0', api: 1,
  workers: {echo: 'echo.js'}, wasm: {broken: 'broken.wasm'}}));
fs.writeFileSync(path.join(componentDir, 'echo.js'),
  'onmessage = event => postMessage(event.data);');
fs.writeFileSync(path.join(componentDir, 'broken.wasm'), Buffer.from([1]));
fs.writeFileSync(path.join(componentDir, 'component.js'), `
ReviewSheet.register({kind: 'custom', version: '1.0.0', api: 1,
  keys: {p: 'ping worker'},
  render(media, api) {
    (window.customApis ||= []).push(api);
    window.customRenderCount = (window.customRenderCount || 0) + 1;
    const box = document.createElement('div');
    const status = document.createElement('span');
    status.textContent = 'Ready'; box.append(status);
    const control = document.createElement('button');
    control.textContent = 'Check sibling and worker';
    control.onclick = async () => {
      try {
        const sibling = await api.media('other', 0);
        if (!sibling.bytes) throw Error('missing sibling bytes');
        const worker = api.worker('echo');
        worker.onmessage = event => { status.textContent = event.data; };
        worker.postMessage('Sibling ready');
        await api.wasm('broken').then(() => {
          throw Error('bad wasm accepted'); },
          () => {});
      } catch (error) { status.textContent = 'Failed: ' + error; }
    };
    box.append(control);
    const fail = document.createElement('button');
    fail.textContent = 'Fail media';
    fail.onclick = () => api.fail('Custom media failed.');
    box.append(fail);
    if (media.alt === 'held') return new Promise(resolve => {
      window.releaseHeldRender = () => { api.ready(); resolve(box); };
    });
    if (media.alt === 'delayed') return new Promise(resolve =>
      setTimeout(() => { api.ready(); resolve(box); }, 150));
    api.ready(); return box;
  },
  key(event, box) {
    if (event.key !== 'p') return false;
    box.querySelector('button').click(); return true;
  },
  focus() { window.customFocused = (window.customFocused || 0) + 1; },
  blur() { window.customBlurred = (window.customBlurred || 0) + 1; },
  dispose(box) {
    window.customDisposed = (window.customDisposed || 0) + 1;
    box.dataset.disposed = 'true';
  }
});`);
const previousConfig = process.env.XDG_CONFIG_HOME;
process.env.XDG_CONFIG_HOME = config;
run('component', 'trust', componentDir, '--yes');
const custom = {review: 'custom-fixture',
  components: {custom: 'custom-component'},
  decisions: {accept: {kind: 'boolean'}},
  groups: [{id: 'pair', items: [
    {id: 'first', media: [{kind: 'custom', src: 'one.txt', mode: 'embed'}]},
    {id: 'other', media: [{kind: 'custom', src: 'one.txt', mode: 'embed',
      alt: 'delayed'}]},
  ]}]};
write('custom.json', JSON.stringify(custom));
run('build', path.join(temp, 'custom.json'), '--output',
  path.join(temp, 'custom.html'));
const pendingCustom = JSON.parse(JSON.stringify(custom));
pendingCustom.review = 'pending-custom';
pendingCustom.groups[0].items[1].media[0].alt = 'held';
write('pending-custom.json', JSON.stringify(pendingCustom));
run('build', path.join(temp, 'pending-custom.json'), '--output',
  path.join(temp, 'pending-custom.html'));
function csp(file) {
  return fs.readFileSync(path.join(temp, file), 'utf8')
    .match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
}
assert.match(csp('custom.html'), /script-src [^;]*'wasm-unsafe-eval'/);
assert.doesNotMatch(csp('review.html'), /wasm-unsafe-eval/);
for (const [fault, prefix, apiValue, version, hooks] of [
  ['missing', 'if (false)', '1', "'1.0.0'", ''],
  ['duplicate', 'for (let i = 0; i < 2; i++)', '1', "'1.0.0'", ''],
  ['api', '', '1 + 1', "'1.0.0'", ''],
  ['version', '', '1', "'1.0.0' + '-mismatch'", ''],
  ['hook', '', '1', "'1.0.0'", "focus: 'invalid',"],
  ['native', '', '1', "'1.0.0'", ''],
]) {
  const directory = path.join(temp, 'fault-' + fault);
  fs.mkdirSync(directory);
  fs.writeFileSync(path.join(directory, 'component.json'), JSON.stringify({
    kind: 'fault', version: '1.0.0', api: 1}));
  fs.writeFileSync(path.join(directory, 'component.js'), `${prefix}
    ReviewSheet.register({kind:'fault', version:${version}, api:${apiValue},
      ${hooks} render(media, api) { api.ready();
        return document.createElement('${fault === 'native' ? 'img' : 'div'}');
      }});`);
  run('component', 'trust', directory, '--yes');
  write('fault-' + fault + '.json', JSON.stringify({review: 'fault-' + fault,
    components: {fault: 'fault-' + fault},
    decisions: {accept: {kind: 'boolean'}},
    items: [{id: 'one', media: [{kind: 'fault', src: 'one.txt',
      mode: 'embed'}]}]}));
  run('build', path.join(temp, 'fault-' + fault + '.json'), '--output',
    path.join(temp, 'fault-' + fault + '.html'));
}
if (previousConfig === undefined) delete process.env.XDG_CONFIG_HOME;
else process.env.XDG_CONFIG_HOME = previousConfig;

async function check(browserType, name, launchOptions = {}) {
  const options = {headless: true, timeout: 10000, ...launchOptions};
  let browser;
  try { browser = await browserType.launch(options); }
  catch (error) {
    const launchError = new Error(name + ' could not launch: ' +
      String(error.message).split('\n')[0]);
    launchError.browserLaunchError = true;
    throw launchError;
  }
  console.log('RUN: ' + name + ' ' + browser.version());
  try {
    await checkPage(browser, name);
    await checkComparison(browser, name);
    await checkCustom(browser, name);
    await checkRegistrationFailures(browser);
    if (browserType === playwright.chromium)
      await checkSyntheticSize(browser, name);
  } finally {
    await browser.close();
  }
  console.log('PASS: ' + name + ' ' + browser.version() +
    ' file:// page and components');
}

async function checkCustom(browser, name) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('file://' + path.join(temp, 'custom.html'));
  await page.locator('[data-item-id="first"] .media')
    .getByRole('button', {name: 'Check sibling and worker'}).click();
  await page.getByText('Sibling ready').first().waitFor();
  assert.equal(await page.evaluate(() => window.customRenderCount), 2);
  await page.getByRole('button', {name: 'Keys (?)', exact: true}).click();
  await page.getByText('p · ping worker', {exact: true}).waitFor();
  await page.getByRole('button', {name: 'Close', exact: true}).click();
  await page.evaluate(() => {
    const input = document.createElement('div');
    input.contentEditable = 'true'; input.id = 'editable-fixture';
    document.body.append(input); input.focus();
  });
  await page.keyboard.press('j');
  assert.equal(await page.locator('.item.focused').getAttribute('data-item-id'),
    'first');
  await page.evaluate(() => {
    document.getElementById('editable-fixture').remove();
  });
  assert.equal(await page.evaluate(() => window.customApis.every(api =>
    Object.isFrozen(api) && Object.isFrozen(api.group) &&
    Object.isFrozen(api.group.members) &&
    api.group.members.every(Object.isFrozen))), true);
  assert.equal(await page.evaluate(async () => {
    const api = window.customApis[0];
    const errors = [];
    try { api.worker('absent'); } catch (error) { errors.push(error.message); }
    try { await api.wasm('absent'); }
    catch (error) { errors.push(error.message); }
    try { await api.media('absent', 0); }
    catch (error) { errors.push(error.message); }
    return errors.length;
  }), 3);
  assert.equal(await page.locator('[data-scope-state="item:first:accept"]')
    .textContent(), 'unanswered');
  await page.keyboard.press('p');
  await page.getByText('Sibling ready').first().waitFor();
  await page.locator('[data-item-id="first"] .media')
    .getByRole('button', {name: 'Fail media'}).click();
  assert.equal(await page.locator('[data-scope-state="item:first:accept"]')
    .textContent(), 'invalid');
  assert.ok(await page.getByText('Custom media failed.').count());
  assert.equal(await page.evaluate(async () => {
    try { await window.customApis[1].media('first', 0); return false; }
    catch (error) { return /failed sibling/.test(error.message); }
  }), true);
  await page.evaluate(() => dispatchEvent(new Event('pagehide')));
  assert.equal(await page.evaluate(() => window.customDisposed), 2);
  assert.equal(await page.evaluate(async () => {
    try { await window.customApis[1].media('first', 0); return false; }
    catch (error) { return /disposed/.test(error.message); }
  }), true);
  assert.ok(!errors.length, name + ': ' + errors.join('\n'));
  await page.close();
  const pending = await browser.newPage();
  await pending.goto('file://' + path.join(temp, 'pending-custom.html'));
  await pending.waitForFunction(() => window.customRenderCount === 2);
  await pending.evaluate(() => {
    window.customApis[0].media('other', 0).then(
      () => { window.pendingSibling = 'unexpected success'; },
      error => { window.pendingSibling = error.message; });
    dispatchEvent(new Event('pagehide'));
    window.releaseHeldRender();
  });
  await pending.waitForFunction(() => /disposed/.test(window.pendingSibling));
  await pending.waitForTimeout(200);
  assert.equal(await pending.evaluate(() => window.customDisposed), 2);
  assert.equal(await pending.locator('[data-item-id="other"] .media span')
    .count(), 0, 'late render attached after disposal');
  await pending.close();
}

async function checkRegistrationFailures(browser) {
  for (const fault of ['missing', 'duplicate', 'api', 'version', 'hook',
    'native']) {
    const page = await browser.newPage();
    await page.goto('file://' + path.join(temp, 'fault-' + fault + '.html'));
    const state = page.locator('[data-scope-state="item:one:accept"]');
    assert.equal(await state.textContent(), 'invalid', fault);
    await page.getByRole('button', {name: 'true', exact: true}).click();
    assert.equal(await state.textContent(), 'invalid', fault);
    assert.ok(await page.locator('#messages .error').count(), fault);
    await page.close();
  }
}

async function checkSyntheticSize(browser, name) {
  write('sized.json', JSON.stringify({review: 'synthetic-sized', groups: [
    comparisonCase(3, 40, 400, 360), comparisonCase(4, 40, 400, 360)]}));
  run('build', path.join(temp, 'sized.json'), '--output',
    path.join(temp, 'sized.html'));
  const session = await browser.newBrowserCDPSession();
  async function rendererRss() {
    const {processInfo} = await session.send('SystemInfo.getProcessInfo');
    const ids = processInfo.filter(row => row.type === 'renderer')
      .map(row => String(row.id));
    assert.ok(ids.length, 'renderer process inventory is empty');
    const result = spawnSync('ps', ['-o', 'rss=', '-p', ids.join(',')],
      {encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
    const values = result.stdout.trim().split(/\s+/).map(Number);
    assert.ok(values.every(value => Number.isFinite(value) && value > 0));
    return values.reduce((sum, value) => sum + value * 1024, 0);
  }
  const page = await browser.newPage();
  await page.goto('about:blank');
  const baseline = await rendererRss();
  let peak = baseline;
  async function sample() { peak = Math.max(peak, await rendererRss()); }
  await page.goto('file://' + path.join(temp, 'sized.html'));
  const first = page.locator('[data-case-id="case-3"]');
  const status = first.locator('.comparison-status');
  await status.getByText(/Display 0/).waitFor();
  await sample();
  for (let index = 1; index < 40; index++) {
    await first.getByLabel('Exact display frame').fill(String(index));
    await status.getByText(new RegExp('Display ' + index + ' ·')).waitFor();
    if (index % 5 === 0) await sample();
  }
  const metrics = await page.evaluate(() => ReviewSheet.metrics());
  assert.ok(metrics.cacheEvictions > 0, 'fixture never exceeded cache limit');
  assert.ok(metrics.decodedTiles <= 32);
  assert.ok(metrics.decodedBytes <= 32 * 400 * 360 * 4);
  // Delay previously unseen frames so preload/cache hits cannot mask races.
  await page.evaluate(() => {
    const decode = Image.prototype.decode;
    let remaining = 2;
    window.delayedDecodes = 0;
    Image.prototype.decode = function () {
      if (remaining-- > 0) {
        window.delayedDecodes++;
        return new Promise((resolve, reject) => setTimeout(() =>
          decode.call(this).then(resolve, reject), 250));
      }
      return decode.call(this);
    };
  });
  await first.getByLabel('Exact display frame').fill('3');
  await page.waitForFunction(() => window.delayedDecodes === 2);
  await first.getByLabel('View').selectOption('side');
  await first.getByLabel('Exact display frame').fill('20');
  await status.getByText(/Display 20 ·/).waitFor();
  await page.waitForTimeout(300);
  assert.deepEqual(await first.locator('.comparison-panel .frame-sequence')
    .evaluateAll(nodes => nodes.map(node => node.dataset.frameIndex)),
  ['20', '20']);
  await first.getByLabel('View').selectOption('front');
  await status.getByText(/Display 20 ·/).waitFor();
  // Frame 1 was evicted above. A delayed scrub to it must not overwrite a
  // subsequent exact step, even when the decode resolves after the step.
  await page.evaluate(() => {
    const decode = Image.prototype.decode;
    let remaining = 2;
    window.evictedDecodes = 0;
    Image.prototype.decode = function () {
      if (remaining-- > 0) {
        window.evictedDecodes++;
        return new Promise((resolve, reject) => setTimeout(() =>
          decode.call(this).then(resolve, reject), 250));
      }
      return decode.call(this);
    };
  });
  await first.getByLabel('Exact display frame').fill('1');
  await page.waitForFunction(() => window.evictedDecodes === 2);
  await first.getByRole('button', {name: 'Next frame'}).click();
  await status.getByText(/Display 21 ·/).waitFor();
  await page.waitForTimeout(300);
  assert.deepEqual(await first.locator('.comparison-panel .frame-sequence')
    .evaluateAll(nodes => nodes.map(node => node.dataset.frameIndex)),
  ['21', '21']);
  await first.getByRole('button', {name: 'Play', exact: true}).click();
  await page.waitForTimeout(200); await sample();
  await page.evaluate(() => document.activeElement.blur());
  await page.keyboard.press('J');
  const second = page.locator('[data-case-id="case-4"]');
  await second.locator('.comparison-status').getByText(/Display 0 ·/)
    .waitFor();
  await sample();
  assert.equal(await first.locator('img[src]').count(), 0);
  await page.reload();
  await first.locator('.comparison-status').getByText(/Display 0 ·/).waitFor();
  await sample();
  assert.ok(peak - baseline < 1024 ** 3,
    'synthetic renderer RSS increase exceeds 1 GiB');
  const htmlBytes = fs.statSync(path.join(temp, 'sized.html')).size;
  const frozen = JSON.parse(fs.readFileSync(
    path.join(temp, 'sized.resolved.json'), 'utf8'));
  const tilePaths = Object.keys(frozen.files).filter(file =>
    file.endsWith('.png'));
  assert.equal(tilePaths.length, 640);
  const tileSizes = tilePaths.map(file => fs.statSync(file).size);
  const embeddedBytes = tileSizes.reduce((total, size) => total + size, 0);
  assert.ok(Math.max(...tileSizes) <= 96 * 1024);
  assert.ok(embeddedBytes <= 128 * 1024 ** 2);
  assert.ok(htmlBytes <= 256 * 1024 ** 2);
  console.log('MEASURE: ' + name + ' synthetic 2 cases, 640 unique ' +
    '400x360 tiles; embedded=' + embeddedBytes + ', max tile=' +
    Math.max(...tileSizes) + ', HTML=' + htmlBytes +
    ' bytes; renderer RSS baseline=' +
    baseline + ', sampled peak=' + peak + ', increase=' +
    Math.max(0, peak - baseline) + ' bytes; decoded cache=' +
    metrics.decodedTiles + ' tiles/' + metrics.decodedBytes +
    ' bytes; evictions=' + metrics.cacheEvictions);
  await page.close(); await session.detach();
}

async function checkComparison(browser, name) {
  const context = await browser.newContext({acceptDownloads: true});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const original = Image.prototype.decode;
    let calls = 0;
    Image.prototype.decode = async function () {
      calls++;
      if (calls <= 2) await new Promise(resolve => setTimeout(resolve, 150));
      return original.call(this);
    };
  });
  await page.goto('file://' + path.join(temp, 'comparison.html'));
  const first = page.locator('[data-case-id="case-1"]');
  await first.locator('.comparison-status').getByText(/Display 0/).waitFor();
  assert.equal(await first.locator('.comparison-panel img').count(), 2);
  assert.equal(await first.locator('.comparison-panel img').first()
    .getAttribute('alt'), 'Numbered frame sequence');
  await first.getByRole('button', {name: 'Next frame'}).click();
  await first.locator('.comparison-status').getByText(/Display 1/).waitFor();
  assert.equal(await first.locator('.comparison-panel img').first()
    .evaluate(image => image.parentElement.dataset.sourceFrame), '0');
  await first.getByLabel('View').selectOption('side');
  await first.locator('.comparison-status').getByText(/Display 1/).waitFor();
  await first.getByLabel('Candidate').selectOption('b1');
  await first.getByLabel('Crop').selectOption('detail');
  await first.getByLabel('Exact display frame').fill('2');
  await first.locator('.comparison-status').getByText(/Display 2/).waitFor();
  assert.equal(await first.locator('.comparison-panel img').last()
    .evaluate(image => image.parentElement.dataset.sourceFrame), '1');
  await first.getByLabel('Playback speed').selectOption('0.125');
  await first.getByLabel('Loop start frame').fill('1');
  await first.getByLabel('Loop end frame exclusive').fill('3');
  await first.getByLabel('Loop end frame exclusive').dispatchEvent('change');
  await first.getByRole('button', {name: 'Play', exact: true}).click();
  await page.waitForTimeout(350);
  await first.getByRole('button', {name: 'Pause', exact: true}).click();
  const loopedIndex = Number(await first.getByLabel('Exact display frame')
    .inputValue());
  assert.ok(loopedIndex >= 1 && loopedIndex < 3);
  await first.getByRole('button', {name: 'Reveal identities'}).click();
  assert.match(await first.locator('.comparison-panel h3').last()
    .textContent(), /Variant two/);
  await page.locator('[data-group-id="case-1"]')
    .getByRole('button', {name: 'B', exact: true}).click();
  async function exportPacket() {
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Export results'}).click();
    return JSON.parse(fs.readFileSync(await (await download).path(), 'utf8'));
  }
  const result = await exportPacket();
  assert.equal(result.answers['group:case-1:pick'].value, 'b1');
  assert.equal(result.answers['group:case-1:pick'].evidence, 'verified');
  assert.equal(result.reveals['case-1'].revealed_before_decision, true);
  const firstReveal = result.reveals['case-1'].first_reveal;
  await page.keyboard.press('u');
  const undone = await exportPacket();
  assert.equal(undone.answers['group:case-1:pick'].state, 'unanswered');
  assert.equal(undone.reveals['case-1'].revealed_before_decision, null);
  assert.equal(undone.reveals['case-1'].decision_revision, 2);
  assert.deepEqual(undone.reveals['case-1'].first_reveal, firstReveal);
  await page.locator('[data-group-id="case-1"]')
    .getByRole('button', {name: 'A', exact: true}).click();
  const revised = await exportPacket();
  assert.equal(revised.reveals['case-1'].decision_revision, 3);
  assert.equal(revised.reveals['case-1'].revealed_before_decision, true);
  await page.reload();
  await first.locator('.comparison-status').getByText(/Display 0/).waitFor();
  assert.doesNotMatch(await first.locator('.comparison-panel h3').last()
    .textContent(), /Variant/);
  await page.locator('#import-file').setInputFiles({name: 'older.json',
    mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(result))});
  await page.getByText('Choose each conflicting answer').waitFor();
  await page.getByRole('button', {name: /Use imported: "b1"/}).click();
  await page.getByRole('button', {name: 'Close'}).click();
  const reimported = await exportPacket();
  assert.ok(reimported.reveals['case-1'].decision_revision > 3);
  assert.deepEqual(reimported.reveals['case-1'].first_reveal, firstReveal);
  await page.evaluate(() => {
    const original = Image.prototype.decode;
    let delayed = 2;
    Image.prototype.decode = function () {
      if (delayed-- > 0) return new Promise((resolve, reject) =>
        setTimeout(() => original.call(this).then(resolve, reject), 200));
      return original.call(this);
    };
  });
  await first.getByLabel('Exact display frame').evaluate(input => {
    input.value = '1'; input.dispatchEvent(new Event('input', {bubbles: true}));
    input.value = '2'; input.dispatchEvent(new Event('input', {bubbles: true}));
  });
  await first.locator('.comparison-status').getByText(/Display 2/).waitFor();
  await page.waitForTimeout(250);
  assert.equal(await first.locator('.comparison-panel img').first()
    .evaluate(image => image.parentElement.dataset.frameIndex), '2');
  await first.getByLabel('Exact display frame').fill('3');
  await page.evaluate(() => document.activeElement.blur());
  await page.keyboard.press('J');
  const second = page.locator('[data-case-id="case-2"]');
  await second.locator('.comparison-status').getByText(/Display 0/).waitFor();
  await page.waitForTimeout(250);
  assert.equal(await second.locator('.comparison-panel img').first()
    .evaluate(image => image.parentElement.dataset.frameIndex), '0');
  const memory = await page.evaluate(() => ({
    cache: ReviewSheet.metrics(),
    heap: performance.memory?.usedJSHeapSize || null}));
  assert.ok(memory.cache.decodedTiles <= 32);
  assert.equal(memory.cache.decodedTileLimit, 32);
  if (memory.heap !== null) assert.ok(memory.heap < 1024 * 1024 * 1024);
  await page.evaluate(() => {
    const key = 'review-sheet:comparison-fixture';
    const draft = JSON.parse(localStorage.getItem(key));
    draft.reveals['case-1'].digest = 'f'.repeat(64);
    localStorage.setItem(key, JSON.stringify(draft));
  });
  await page.reload();
  await page.getByText(/Stored comparison reveal history is stale/).waitFor();
  await first.locator('.comparison-status').getByText(/Display 0/).waitFor();
  const staleReveal = await exportPacket();
  assert.equal(staleReveal.reveals['case-1'].first_reveal, null);
  assert.equal(staleReveal.answers['group:case-1:pick'].state, 'stale');
  assert.ok(!errors.length, name + ': ' + errors.join('\n'));
  await context.close();
}

async function checkPage(browser, name) {
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
  await page.locator('.media img').first().evaluate(image => image.decode());
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
  await page.keyboard.press('Control+j');
  assert.equal(await page.locator('.item.focused').getAttribute('data-item-id'),
    'motion');
  for (const modifier of ['Control', 'Meta', 'Alt']) {
    for (const key of ['j', 'k', 'n']) {
      await page.keyboard.press(modifier + '+' + key);
      assert.equal(await page.locator('.item.focused')
        .getAttribute('data-item-id'), 'motion');
    }
  }
  await page.keyboard.press('n');
  assert.equal(await page.locator('.item.focused').getAttribute('data-item-id'),
    'sound');
  await page.keyboard.press('?');
  assert.ok(await page.getByText('Keyboard controls').count());
  await page.getByRole('button', {name: 'Close'}).click();
  const draftWithOrphan = JSON.parse(await page.evaluate(() =>
    localStorage.getItem('review-sheet:browser-fixture')));
  draftWithOrphan.answers['item:gone:verdict'] = {
    state: 'answered', value: 'keep', digest: '0'.repeat(64)};
  await page.evaluate(raw => localStorage.setItem(
    'review-sheet:browser-fixture', raw), JSON.stringify(draftWithOrphan));
  await page.reload();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name: 'Discard stale draft entries'}).click();
  const cleaned = JSON.parse(await page.evaluate(() =>
    localStorage.getItem('review-sheet:browser-fixture')));
  assert.ok(!cleaned.answers['item:gone:verdict']);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export results'}).click();
  const download = await downloadPromise;
  const packet = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
  assert.equal(packet.answers['item:still:verdict'].value, 'keep');
  assert.equal(packet.answers['item:motion:verdict'].state, 'unanswered');
  const invalid = {...packet, answers: {...packet.answers,
    'item:still:verdict': {...packet.answers['item:still:verdict'],
      value: 'unsupported'}}};
  await page.locator('#import-file').setInputFiles({name: 'invalid.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(invalid))});
  await page.getByText(/invalid or stale imported answers/).waitFor();
  assert.match(await page.locator('#messages').textContent(),
    /rejected|invalid/i);
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'answered');
  const stale = {...packet, answers: {...packet.answers,
    'item:still:verdict': {...packet.answers['item:still:verdict'],
      digest: '0'.repeat(64)}}};
  await page.locator('#import-file').setInputFiles({name: 'stale.json',
    mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(stale))});
  await page.getByText(/invalid or stale imported answers/).last().waitFor();
  assert.match(await page.locator('#messages').textContent(), /stale/i);
  assert.equal(await page.locator('[data-scope-state="item:still:verdict"]')
    .textContent(), 'answered');
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
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(incoming))});
  await page.getByText('Choose each conflicting answer').waitFor();
  assert.ok(await page.getByText('Choose each conflicting answer').count());
  assert.ok(await page.getByRole('button', {name: /Keep draft: "keep"/})
    .count());
  await page.getByRole('button', {name: 'Close'}).click();
  const authorityPage = await context.newPage();
  await authorityPage.goto('file://' + path.join(temp, 'authority.html'));
  await authorityPage.getByRole('button', {name: 'true'}).click();
  assert.equal(await authorityPage.locator(
    '[data-scope-state="item:one:approve"]')
    .textContent(), 'unanswered');
  await authorityPage.getByRole('button', {name: /Confirm true/}).click();
  assert.equal(await authorityPage.locator(
    '[data-scope-state="item:one:approve"]')
    .textContent(), 'answered');
  const authorityDownload = authorityPage.waitForEvent('download');
  await authorityPage.getByRole('button', {name: 'Export results'}).click();
  const authorityPacket = JSON.parse(fs.readFileSync(
    await (await authorityDownload).path(), 'utf8'));
  assert.equal(authorityPacket.answers['item:one:approve'].media_hashes[
    'one.png'], crypto.createHash('sha256').update(png).digest('hex'));
  await authorityPage.evaluate(() => localStorage.removeItem(
    'review-sheet:authority-fixture'));
  await authorityPage.reload();
  const wrongHash = structuredClone(authorityPacket);
  wrongHash.answers['item:one:approve'].media_hashes['one.png'] =
    '0'.repeat(64);
  await authorityPage.locator('#import-file').setInputFiles({
    name: 'wrong-hash.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(wrongHash))});
  await authorityPage.getByText(/invalid or stale imported answers/).waitFor();
  assert.equal(await authorityPage.locator(
    '[data-scope-state="item:one:approve"]').textContent(), 'unanswered');
  await authorityPage.locator('#import-file').setInputFiles({
    name: 'authority.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(authorityPacket))});
  assert.equal(await authorityPage.locator(
    '[data-scope-state="item:one:approve"]').textContent(), 'inherited');
  await authorityPage.getByRole('button', {name: 'true'}).click();
  await authorityPage.getByRole('button', {name: /Confirm true/}).click();
  assert.equal(await authorityPage.locator(
    '[data-scope-state="item:one:approve"]').textContent(), 'answered');
  const delayedPage = await context.newPage();
  await delayedPage.addInitScript(() => {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    crypto.subtle.digest = async (...args) => {
      await new Promise(resolve => setTimeout(resolve, 750));
      return digest(...args);
    };
  });
  await delayedPage.goto('file://' + path.join(temp, 'authority.html'));
  const delayedDownload = delayedPage.waitForEvent('download');
  await delayedPage.getByRole('button', {name: 'Export results'}).click();
  const delayedPacket = JSON.parse(fs.readFileSync(
    await (await delayedDownload).path(), 'utf8'));
  assert.equal(delayedPacket.answers['item:one:approve'].state, 'answered');
  await delayedPage.evaluate(() => localStorage.removeItem(
    'review-sheet:authority-fixture'));
  await delayedPage.reload();
  await delayedPage.locator('#import-file').setInputFiles({
    name: 'authority.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(authorityPacket))});
  await delayedPage.getByText('inherited', {exact: true}).waitFor();
  await authorityPage.evaluate(packet => localStorage.setItem(
    'review-sheet:authority-fixture', JSON.stringify(packet)),
    {schema_version: 1, review: 'authority-fixture',
      answers: authorityPacket.answers});
  const corruptedPage = await context.newPage();
  await corruptedPage.goto('file://' + path.join(temp,
    'authority-corrupt.html'));
  assert.ok(await corruptedPage.getByText(/verification failed/).count());
  await corruptedPage.getByRole('button', {name: 'true'}).click();
  await corruptedPage.getByRole('button', {name: /Confirm true/}).click();
  assert.equal(await corruptedPage.locator(
    '[data-scope-state="item:one:approve"]').textContent(), 'invalid');
  const failedDownload = corruptedPage.waitForEvent('download');
  await corruptedPage.getByRole('button', {name: 'Export results'}).click();
  const failedPacket = JSON.parse(fs.readFileSync(
    await (await failedDownload).path(), 'utf8'));
  assert.equal(failedPacket.answers['item:one:approve'].state, 'unanswered');
  assert.match(await corruptedPage.locator('#messages').textContent(),
    /exported as unanswered/);
  const noStoragePage = await context.newPage();
  await noStoragePage.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {get() {
      throw Error('storage denied');
    }});
  });
  await noStoragePage.goto(url);
  assert.match(await noStoragePage.locator('#messages').textContent(),
    /Draft storage is unavailable/);
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
}

(async () => {
  let passed = 0;
  let failed = 0;
  try {
    try {
      await check(playwright.chromium, 'Google Chrome', {channel: 'chrome'});
      passed++;
    } catch (error) {
      const absent = error.browserLaunchError &&
        /Chromium distribution 'chrome' is not found|executable doesn't exist/i
          .test(error.message);
      if (!absent) {
        failed++;
        console.error('FAIL: Google Chrome browser checks');
        console.error(error);
      } else {
        console.log('SKIP: Google Chrome is absent: ' + error.message);
        const executable = playwright.chromium.executablePath();
        if (!fs.existsSync(executable)) {
          console.log('SKIP: bundled Chromium executable absent: ' +
            executable);
        } else {
          try {
            await check(playwright.chromium, 'Bundled Chromium');
            passed++;
          } catch (fallbackError) {
            failed++;
            console.error('FAIL: bundled Chromium browser checks');
            console.error(fallbackError);
          }
        }
      }
    }
    const firefoxPath = process.env.REVIEW_SHEET_FIREFOX_EXECUTABLE ||
      playwright.firefox.executablePath();
    if (!fs.existsSync(firefoxPath)) {
      console.log('SKIP: bundled Firefox executable absent: ' + firefoxPath);
    } else {
      try {
        await check(playwright.firefox, 'Firefox',
          process.env.REVIEW_SHEET_FIREFOX_EXECUTABLE ?
            {executablePath: firefoxPath} : {});
        passed++;
      } catch (error) {
        if (error.browserLaunchError) {
          console.log('SKIP: ' + error.message);
        } else {
          failed++;
          console.error('FAIL: Firefox browser checks');
          console.error(error);
        }
      }
    }
    if (failed) process.exitCode = 1;
    else if (!passed) process.exitCode = 77;
    if (passed) console.log('PASS: ' + passed + ' available browser(s)');
  } finally {
    fs.rmSync(temp, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exit(1); });
