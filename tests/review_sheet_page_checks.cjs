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
      decisions_review: {summary: {kind: 'text', question: 'Review note'}},
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
    description.groups[0].decisions = {
      rating: {kind: 'number', question: 'Group rating'},
      approved: {kind: 'boolean'}};
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
    await test('Escape leaves group and review answers', async page => {
      const card = page.locator('.item.focused');
      for (const label of ['Group rating', 'Review note']) {
        const input = page.getByLabel(label, {exact: true});
        await input.focus();
        await input.press('Escape');
        assert(await card.evaluate(node => node === document.activeElement),
          label);
      }
    });
    await test('Escape skips a hidden card', async page => {
      await page.locator('.item.focused').evaluate(node => {
        node.style.display = 'none';
      });
      const input = page.getByLabel('Group rating', {exact: true});
      await input.focus();
      await input.press('Escape');
      assert(await input.evaluate(node =>
        node.closest('.decision') === document.activeElement));
    });
    await test('both boolean selections have a visible mark', async page => {
      const buttons = page.locator('[data-choice="group:first:approved"]');
      for (const value of ['true', 'false']) {
        const selected = buttons.filter({hasText: new RegExp('^' + value + '$')});
        const other = buttons.filter({hasNotText: value});
        await selected.click();
        assert.equal(await selected.getAttribute('aria-pressed'), 'true');
        assert.equal(await other.getAttribute('aria-pressed'), 'false');
        assert((await selected.evaluate(node =>
          getComputedStyle(node, '::before').content)).includes('✓'));
      }
    });
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
      assert((await page.locator('#messages').textContent()).length > 0);
    });
    await test('messages remain visible after scrolling', async page => {
      for (const [width, height] of [[1440, 900], [1280, 720]]) {
        await page.setViewportSize({width, height});
        const card = page.locator('.item').last();
        const number = card.locator('input[type=number]');
        await number.click({position: {x: 10, y: 10}});
        await number.fill('6');
        await card.locator('.decision').last().getByRole('button',
          {name: 'Save answer', exact: true}).click();
        const message = page.locator('#messages .banner').last();
        assert((await message.textContent()).includes('outside its bounds'));
        const bounds = await message.boundingBox();
        assert(bounds.y >= 0 && bounds.y + bounds.height <= height,
          JSON.stringify(bounds));
        assert.equal(await page.locator('#messages').getAttribute('aria-live'),
          'polite');
        assert.equal(await message.getAttribute('role'), 'alert');
        assert((await message.textContent()).startsWith('Error: '));
        assert((await page.locator('.toolbar').boundingBox()).height <= 48);
        await number.click({position: {x: 10, y: 10}});
        assert.equal(await page.locator('#messages .banner').count(), 0);
        // Keyboard save raises the same error and next keyboard action clears it.
        await page.keyboard.press('Control+Enter');
        assert.equal(await message.count(), 1);
        await page.keyboard.press('Escape');
        assert.equal(await message.count(), 0);
      }
    });
    const imageDescription = {
      review: 'image-fixture',
      items: [{id: 'gallery', media: [
        {kind: 'image', src: 'wide.svg', mode: 'embed', alt: 'Wide caption'},
        {kind: 'text', src: 'sample.txt'},
        {kind: 'image', src: 'tall.svg', mode: 'embed'},
        {kind: 'image', src: 'third.svg', mode: 'embed', alt: 'Third caption'}]},
      {id: 'single', media: [{kind: 'image', src: 'wide.svg', mode: 'embed'}]}],
      groups: [{id: 'alternatives', items: [
        {id: 'variant-a', title: 'First sample', media: [
          {kind: 'image', src: 'wide.svg',
          mode: 'embed', alt: 'Variant A'}]},
        {id: 'variant-b', title: 'Second sample', media: [
          {kind: 'image', src: 'tall.svg',
          mode: 'embed', alt: 'Variant B'}]}]}]};
    for (const [name, width, height] of [
      ['wide', 800, 400], ['tall', 400, 800], ['third', 600, 300]]) {
      fs.writeFileSync(path.join(temp, name + '.svg'),
        `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" ` +
        `height="${height}"><rect width="100%" height="100%" ` +
        'fill="teal"/></svg>');
    }
    fs.writeFileSync(path.join(temp, 'sample.txt'), 'Local text');
    const imageInput = path.join(temp, 'images.json');
    const imagePage = path.join(temp, 'images.html');
    fs.writeFileSync(imageInput, JSON.stringify(imageDescription));
    const imageBuilt = spawnSync(path.join(root, 'bin/review-sheet'),
      ['build', imageInput, '--output', imagePage], {encoding: 'utf8'});
    assert.equal(imageBuilt.status, 0, imageBuilt.stderr);
    await test('multi-image compare cycles, zooms and closes', async page => {
      await page.goto('file://' + imagePage);
      const card = page.locator('[data-item-id=gallery]');
      const media = card.locator('.media').first();
      await media.getByRole('button', {name: 'Compare', exact: true}).click();
      await media.locator('img').nth(1).waitFor();
      assert.equal(await media.locator('img').count(), 2);
      assert.deepEqual(await media.locator('figcaption').allTextContents(),
        ['Wide caption', '2 of 3']);
      const firstUrl = await media.locator('img').first().getAttribute('src');
      const secondUrl = await media.locator('img').last().getAttribute('src');
      const scales = await media.locator('img').evaluateAll(nodes => nodes.map(
        node => node.getBoundingClientRect().width / node.naturalWidth));
      assert(Math.abs(scales[0] - scales[1]) < .001, JSON.stringify(scales));
      const image = media.locator('img').first();
      const before = await image.boundingBox();
      await media.locator('img').last().click();
      assert((await image.boundingBox()).width > before.width);
      await page.keyboard.press('z');
      assert(Math.abs((await image.boundingBox()).width - before.width) < 1);
      await media.getByRole('button', {name: 'Compare', exact: true}).click();
      await media.getByText('Third caption', {exact: true}).waitFor();
      assert.notEqual(await media.locator('img').last().getAttribute('src'),
        secondUrl);
      assert.equal(await media.locator('img').first().getAttribute('src'),
        firstUrl);
      await media.getByRole('button', {name: 'Compare', exact: true}).click();
      await media.getByText('2 of 3', {exact: true}).waitFor();
      await media.getByRole('button', {name: 'Close comparison'}).click();
      assert.equal(await media.locator('img').count(), 1);
      assert.equal(await media.getByRole('button',
        {name: 'Close comparison'}).count(), 0);
      // The last image wraps to the first; z follows the focused media control.
      const last = card.locator('.media').last();
      await last.getByRole('button', {name: 'Compare', exact: true}).click();
      await last.getByText('Wide caption', {exact: true}).waitFor();
      await page.keyboard.press('z');
      assert(await last.locator('.image-view').evaluate(node =>
        node.classList.contains('zoomed')));
      assert(!await media.locator('.image-view').evaluate(node =>
        node.classList.contains('zoomed')));
      await last.getByRole('button', {name: 'Close comparison'}).click();
    });
    await test('single image has no Compare', async page => {
      await page.goto('file://' + imagePage);
      const media = page.locator('[data-item-id=single] .media');
      await media.locator('img').waitFor();
      assert.equal(await media.getByRole('button', {name: 'Compare'}).count(), 0);
      await media.locator('img').click();
      assert.equal(await media.locator('img').evaluate(node =>
        node.style.maxHeight), 'none');
    });
    await test('Compare advances past a failed decode', async page => {
      await page.addInitScript(() => {
        const decode = HTMLImageElement.prototype.decode;
        HTMLImageElement.prototype.decode = function () {
          if (this.alt === '2 of 3') return Promise.reject(Error('fixture'));
          return decode.call(this);
        };
      });
      await page.goto('file://' + imagePage);
      const media = page.locator('[data-item-id=gallery] .media').first();
      const compare = media.getByRole('button', {name: 'Compare', exact: true});
      await compare.click();
      await page.getByText('Comparison image is unavailable.').waitFor();
      assert.equal(await media.locator('img').count(), 1);
      await compare.click();
      await media.getByText('Third caption', {exact: true}).waitFor();
      await compare.click();
      assert.deepEqual(await media.locator('figcaption').allTextContents(),
        ['Wide caption', 'Third caption']);
    });
    await test('ordinary group single images have no Compare', async page => {
      await page.goto('file://' + imagePage);
      for (const id of ['variant-a', 'variant-b']) {
        const media = page.locator(`[data-item-id=${id}] .media`);
        await media.locator('img').waitFor();
        assert.equal(await media.getByRole('button',
          {name: 'Compare', exact: true}).count(), 0);
      }
    });
    await test('configured comparison labels the source item', async page => {
      const configured = JSON.parse(JSON.stringify(imageDescription));
      const group = configured.groups[0];
      group.comparison = {reference: 'variant-a', candidates: ['variant-b'],
        views: ['front'], fps: [30, 1], motion_frames: 1, hold_start: 0,
        hold_end: 0, frame_count: 1, key_frames: [0],
        crops: {full: [0, 0, 800, 400]}};
      group.items[0].media.push({kind: 'image', src: 'third.svg', mode: 'embed',
        alt: 'Own extra image'});
      fs.writeFileSync(imageInput, JSON.stringify(configured));
      const built = spawnSync(path.join(root, 'bin/review-sheet'),
        ['build', imageInput, '--output', imagePage], {encoding: 'utf8'});
      assert.equal(built.status, 0, built.stderr);
      await page.goto('file://' + imagePage);
      const media = page.locator('[data-item-id=variant-a] .media').first();
      await media.getByRole('button', {name: 'Compare', exact: true}).click();
      await media.locator('img').nth(1).waitFor();
      assert.deepEqual(await media.locator('figcaption').allTextContents(),
        ['Variant A', 'Second sample: Variant B']);
      await media.getByRole('button', {name: 'Close comparison'}).click();
      assert.equal(await media.locator('img').count(), 1);
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
