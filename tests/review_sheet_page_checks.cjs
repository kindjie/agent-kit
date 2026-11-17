/* Real pointer/keyboard regressions shared by the browser gate. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

async function checkPageLayout(browser, root) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'review-page-'));
  const context = await browser.newContext({acceptDownloads: true});
  const failures = [];
  try {
    const description = {
      review: 'page-fixture', title: 'A review with a long introduction',
      instructions: 'Read these instructions. '.repeat(35),
      caveats: ['A caveat. '.repeat(20)],
      decisions: {
        verdict: {kind: 'choice', options: ['keep', 'revise'], keys: '12',
          required: true},
        note: {kind: 'text'}, score: {kind: 'number', min: 0, max: 5}},
      groups: ['first', 'second'].map(id => ({id, title: id + ' group',
        items: [0, 1].map(index => ({id: id + index, title: 'Sample item',
          fields: {Context: 'Readable prose. '.repeat(35),
            Command: 'Run `sample --check` and `<img src=x onerror=alert(1)>`.'
              + ' Keep <script> literal and an unmatched `tick.'},
          media: []}))}))};
    const input = path.join(temp, 'description.json');
    const output = path.join(temp, 'page.html');
    fs.writeFileSync(input, JSON.stringify(description));
    const built = spawnSync(path.join(root, 'bin/review-sheet'),
      ['build', input, '--output', output], {encoding: 'utf8'});
    assert.equal(built.status, 0, built.stderr);
    async function test(name, check) {
      const page = await context.newPage();
      page.setDefaultTimeout(4000);
      await page.setViewportSize({width: 1440, height: 900});
      try {
        await page.goto('file://' + output);
        await check(page);
        console.log('PASS: page ' + name);
      } catch (error) {
        failures.push(name + ': ' + error.message);
        console.error('FAIL: page ' + name + ': ' + error.message);
      } finally { await page.close(); }
    }
    await test('pointer and Tab focus', async page => {
      const card = page.locator('.item').nth(1);
      const note = card.locator('textarea');
      await note.click();
      assert(await note.evaluate(node => node === document.activeElement));
      await page.keyboard.type('pointer note');
      assert.equal(await note.inputValue(), 'pointer note');
      assert.equal(await card.getAttribute('class'), 'item focused');
      const number = card.locator('input[type=number]');
      await number.click();
      await page.keyboard.press('ControlOrMeta+a');
      await page.keyboard.type('3');
      assert.equal(await number.inputValue(), '3');
      await card.click({position: {x: 4, y: 4}});
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.tagName),
        'BUTTON');
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      assert(await note.evaluate(node => node === document.activeElement));
      await page.keyboard.type(' tab');
      assert.equal(await note.inputValue(), 'pointer note tab');
    });
    await test('save and leave text', async page => {
      const card = page.locator('.item').first();
      const note = card.locator('textarea');
      // Tab reaches text independently of the pointer-focus regression.
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      await page.keyboard.type('saved note');
      await page.keyboard.press('Meta+Enter');
      const state = note.locator('xpath=ancestor::*[contains(@class,"decision")]')
        .locator('.state');
      assert.equal(await state.textContent(), 'answered');
      await page.keyboard.type(' again');
      await page.keyboard.press('Control+Enter');
      await page.keyboard.press('Escape');
      assert(await card.evaluate(node => node === document.activeElement));
      await page.keyboard.press('1');
      assert.equal(await card.locator('[data-choice]').first()
        .getAttribute('aria-pressed'), 'true');
    });
    await test('component controls retain focus', async page => {
      const card = page.locator('.item').last();
      // Exercise the host boundary with controls a trusted component can add.
      await card.evaluate(node => {
        for (const tag of ['select', 'button', 'audio', 'video', 'a']) {
          const control = document.createElement(tag);
          control.dataset.probe = tag;
          if (tag === 'select') control.add(new Option('Sample', 'sample'));
          else if (tag === 'audio' || tag === 'video') control.controls = true;
          else control.textContent = 'Sample ' + tag;
          if (tag === 'a') {
            control.href = '#';
            control.addEventListener('click', event => event.preventDefault());
          }
          node.append(control);
        }
      });
      for (const tag of ['select', 'button', 'audio', 'video', 'a']) {
        const control = card.locator('[data-probe=' + tag + ']');
        await control.click();
        assert(await control.evaluate(node => node === document.activeElement),
          tag + ' lost keyboard focus');
        assert((await card.getAttribute('class')).includes('focused'));
        await page.keyboard.press('Escape');
      }
    });
    await test('slim pinned bar and focus mode', async page => {
      for (const [width, height] of [[1440, 900], [1280, 720]]) {
        await page.setViewportSize({width, height});
        await page.keyboard.press('J');
        const dimensions = await page.evaluate(() => {
          const bar = document.querySelector('.toolbar');
          const card = document.querySelector('.item.focused');
          return {bar: bar.getBoundingClientRect().toJSON(),
            card: card.getBoundingClientRect().toJSON(),
            header: document.querySelector('header').getBoundingClientRect()
              .toJSON()};
        });
        assert(dimensions.bar.height <= 48, JSON.stringify(dimensions));
        assert.equal(Math.round(dimensions.bar.top), 0);
        assert((await page.locator('#focused-group').textContent())
          .endsWith(' group'));
        assert(dimensions.header.bottom <= 0);
        assert(dimensions.card.top >= dimensions.bar.bottom,
          JSON.stringify(dimensions));
        await page.keyboard.press('f');
        assert(await page.locator('.item.focused').isVisible());
        assert((await page.locator('.item.focused').boundingBox()).y >=
          dimensions.bar.bottom);
        await page.keyboard.press('f');
      }
    });
    await test('sheet download identity', async page => {
      const download = page.waitForEvent('download');
      await page.getByRole('button', {name: 'Export results', exact: true})
        .click();
      assert.equal((await download).suggestedFilename(),
        'page-fixture-results.json');
      assert((await page.locator('#messages').textContent())
        .includes('page-fixture-results.json'));
    });
    await test('safe inline code', async page => {
      const field = page.locator('.item').first().locator('dd').nth(1);
      assert.deepEqual(await field.locator('code').allTextContents(),
        ['sample --check', '<img src=x onerror=alert(1)>']);
      assert.equal(await field.locator('img,script').count(), 0);
      assert((await field.textContent()).endsWith('unmatched `tick.'));
    });
    await test('selected answer has a visible mark', async page => {
      await page.keyboard.press('2');
      const chosen = page.locator('.item.focused [aria-pressed=true]');
      assert((await chosen.evaluate(node =>
        getComputedStyle(node, '::before').content)).includes('✓'));
    });
    await test('reading measure and dark mode', async page => {
      const field = page.locator('.item').first().locator('.fields');
      const measure = await field.evaluate(node => ({
        width: node.getBoundingClientRect().width,
        ch: parseFloat(getComputedStyle(node).fontSize)}));
      assert(measure.width < 900, JSON.stringify(measure));
      await page.emulateMedia({colorScheme: 'dark'});
      assert.notEqual(await page.locator('body').evaluate(node =>
        getComputedStyle(node).backgroundColor), 'rgb(244, 245, 247)');
      assert.equal(await page.locator('details').count(), 0);
    });
    await test('prompt, field placement and collapsed details', async page => {
      fs.writeFileSync(path.join(temp, 'sample.txt'), 'Full-width media');
      const structured = JSON.parse(JSON.stringify(description));
      const item = structured.groups[0].items[0];
      item.fields = {Prompt: 'Choose `sample`?', Context: 'Some context.',
        Recommended: 'Keep the sample.', Careful: 'Confirm before acting.',
        Record: 'Background record.', Path: 'sample.txt'};
      item.media = [{kind: 'text', src: 'sample.txt'}];
      const shapedInput = path.join(temp, 'structured.json');
      const shapedPage = path.join(temp, 'structured.html');
      for (const [before, after] of [
        [['Context'], ['Recommended', 'Careful']],
        [[], ['Context', 'Recommended', 'Careful']],
        [['Context', 'Recommended', 'Careful'], []]]) {
        item.presentation = {prompt: 'Prompt', before_media: before,
          after_media: after};
        fs.writeFileSync(shapedInput, JSON.stringify(structured));
        const result = spawnSync(path.join(root, 'bin/review-sheet'),
          ['build', shapedInput, '--output', shapedPage], {encoding: 'utf8'});
        assert.equal(result.status, 0, result.stderr);
        await page.goto('file://' + shapedPage);
        const card = page.locator('.item').first();
        assert.equal(await card.locator('h3').textContent(), 'Choose sample?');
        assert.equal(await card.locator('h3 code').textContent(), 'sample');
        const order = await card.evaluate(node => [...node.children].map(
          child => child.matches('.media') ? 'media' :
            child.matches('dl') ? child.querySelector('dt').textContent :
              child.tagName.toLowerCase()));
        assert.deepEqual(order.slice(0, 1 + !!before.length + 1 + !!after.length),
          ['h3', ...(before.length ? [before[0]] : []), 'media',
            ...(after.length ? [after[0]] : [])]);
        assert.equal(order.at(-1), 'details');
        const details = card.locator('details');
        assert.equal(await details.getAttribute('open'), null);
        assert.equal(await details.locator('dd').first().isVisible(), false);
        assert.deepEqual(await details.locator('dt').allTextContents(),
          ['Record', 'Path']);
        await details.locator('summary').click();
        assert.equal(await details.locator('dd').first().isVisible(), true);
        assert((await details.textContent()).includes(item.title));
        assert.equal(await card.locator('.media').boundingBox().then(box =>
          box.width) > await card.locator('.fields').first().boundingBox()
          .then(box => box.width), true);
      }
    });
  } finally {
    await context.close();
    fs.rmSync(temp, {recursive: true, force: true});
  }
  assert.deepEqual(failures, [], failures.join('\n'));
}
module.exports = checkPageLayout;

if (require.main === module) (async () => {
  const {chromium} = require('playwright');
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try { await checkPageLayout(browser, process.argv[2]); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
