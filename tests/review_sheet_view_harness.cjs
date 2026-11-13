/* Shipped-page contracts in a tiny DOM; no browser/media performance claim. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const digest = 'a'.repeat(64);
function fixture(options = {}) {
  const nodes = [], documentEvents = new Map(), windowEvents = new Map();
  const log = [], exports = new Map();
  let active;
  function matches(node, selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      if (part.startsWith('#')) return node.id === part.slice(1);
      if (part.startsWith('.')) return part.slice(1).split('.').every(name =>
        node.className.split(/\s+/).includes(name));
      if (part === '[data-item-id]') return node.dataset.itemId !== undefined;
      if (part.startsWith('[')) return false;
      return node.tagName.toLowerCase() === part;
    });
  }
  function element(tag) {
    const node = {tagName: tag.toUpperCase(), children: [], events: new Map(),
      dataset: {}, className: '', value: '', hidden: false, parentNode: null,
      append(...children) { for (const child of children) {
        child.remove?.(); this.children.push(child); child.parentNode = this;
      }},
      remove() { if (this.parentNode) {
        const parent = this.parentNode; this.parentNode = null;
        parent.children.splice(parent.children.indexOf(this), 1);
      }},
      replaceChildren(...children) {
        for (const child of [...this.children]) child.remove();
        this.append(...children);
      },
      addEventListener(type, callback, settings = {}) {
        if (!this.events.has(type)) this.events.set(type, []);
        this.events.get(type).push(callback);
        settings.signal?.addEventListener('abort', () => {
          this.events.set(type,
            this.events.get(type).filter(v => v !== callback));
        }, {once: true});
      },
      setAttribute(name, value) { this[name] = value; },
      matches(selector) { return matches(this, selector); },
      querySelectorAll(selector) {
        return this.children.flatMap(child => [
          ...(matches(child, selector) ? [child] : []),
          ...child.querySelectorAll(selector)]);
      },
      querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
      },
      focus() { active = this; }, blur() { active = null; },
      scrollIntoView() {}, pause() { log.push('media-pause'); },
      click() { return this.events.get('click')?.[0](); }};
    node.classList = {
      contains: name => node.className.split(/\s+/).includes(name),
      add(name) { if (!this.contains(name)) node.className += ' '+name; },
      remove(name) { node.className = node.className.split(/\s+/)
        .filter(value => value !== name).join(' '); },
      toggle(name, force) {
        const on = force === undefined ? !this.contains(name) : force;
        this[on ? 'add' : 'remove'](name); return on;
      }};
    nodes.push(node); return node;
  }
  const app = element('main'); app.id = 'app';
  const body = element('body'); body.append(app);
  const find = id => nodes.find(node => node.id === id);
  const data = {description: {review: 'view-fixture', items: [
    {id: 'first', title: 'First', media: []},
    {id: 'second', title: 'Second', media: []}]},
    scopes: {}, components: {}, limits: {results: 16384},
    resolved_sha256: digest, editor: {sha256: digest, api: 2,
      kind: 'fixture-view', version: '1.0.0', parameters: {},
      source_identity: 'fictional:view', visible_item_ids: ['first', 'second'],
      initial_state: 'baseline'}};
  if (options.groups) {
    data.description.items = [];
    data.description.groups = [
      {id: 'a', title: 'A', items: [{id:'first',title:'First',media:[]}]},
      {id: 'b', title: 'B', items: [{id:'second',title:'Second',media:[]},
        {id:'third',title:'Third',media:[]}]},
      {id: 'empty', title: 'Empty', items: []}];
    data.editor.visible_item_ids.push('third');
  }
  if (options.api) data.editor.api = options.api;
  if (options.binding) {
    data.description = structuredClone(options.binding.description);
    data.resolved_sha256 = options.binding.resolved_sha256;
    data.editor = structuredClone(options.binding.editor);
  }
  const packet = raw => ({schema_version: 2, review: data.description.review,
    resolved_sha256: data.resolved_sha256, answers: {}, project_state: {
      schema_version: 1, editor_sha256: data.editor.sha256, data: raw}});
  if (options.limit) data.limits.results = options.limit;
  if (options.limit && !options.binding) {
    data.limits.results = options.limit;
    const prefix = options.rawPrefix || '';
    const base = {...packet(prefix), context:{}, reviewer:''};
    data.editor.initial_state = prefix+'x'.repeat(options.limit-
      Buffer.byteLength(JSON.stringify(base), 'utf8'));
  }
  const savedPacket = packet('restored');
  if (options.savedReviewer !== undefined)
    savedPacket.reviewer = options.savedReviewer;
  let stored = options.saved === false ? null : JSON.stringify(savedPacket);
  let raw, context, factoryHost, disposed = 0, undoCalls = 0;
  let mutations = 0, prepares = 0;
  const invoke = (hook, value) => {
    log.push([hook, value]);
    if (options.fail === hook) throw Error('fixture '+hook+' failed');
    if (options.async === hook)
      return Promise.reject(Error('fixture '+hook+' pending'));
  };
  const host = {TextEncoder, TextDecoder, structuredClone, AbortController,
    Blob, console, REVIEW_SHEET_DATA: data,
    URL: {createObjectURL(blob) { const url = 'blob:fixture:'+exports.size;
      exports.set(url, blob); return url; }, revokeObjectURL() {}},
    setTimeout: () => 1, clearTimeout() {},
    prompt: () => options.query ?? null,
    localStorage: {getItem() {
      if (options.readFailure) throw Error('fixture own-key unreadable');
      return stored;
    }, setItem(_, value) { stored = value; }},
    document: {createElement: element, getElementById: find, body,
      get activeElement() { return active || body; },
      querySelector: selector => matches(body, selector) ? body :
        body.querySelector(selector),
      querySelectorAll: selector => body.querySelectorAll(selector),
      addEventListener(type, callback) {
        if (!documentEvents.has(type)) documentEvents.set(type, []);
        documentEvents.get(type).push(callback);
      }},
    addEventListener(type, callback) {
      if (!windowEvents.has(type)) windowEvents.set(type, []);
      windowEvents.get(type).push(callback);
    }};
  host.window = host;
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(process.argv[2]),
    'review_sheet_editor.js'), 'utf8'), host);
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), host);
  assert.doesNotThrow(() => host.ReviewSheet.registerEditor({
    kind: data.editor.kind,
    version: '1.0.0', api: data.editor.api, create(binding, callbacks) {
      raw = binding.initial_state; factoryHost = callbacks;
      return {snapshot: () => raw, prepare(next) {
        prepares++;
        if (options.reject && next.includes(options.reject))
          throw Error('fixture foreign owner binding');
        const before = raw, candidate = next+(options.candidateSuffix || '');
        return {snapshot: () => candidate, commit() {
          raw = candidate;
          if (options.unrecovered) throw Error('fixture commit after swap');
        }, rollback() {
          if (options.unrecovered) throw Error('fixture rollback failure');
          raw = before;
        }};
      }, undo() {
        undoCalls++;
        if (options.undoFail) throw Error('fixture undo failed');
        if (options.undoAsync)
          return Promise.reject(Error('fixture undo pending'));
        raw = 'undone'; callbacks.changed();
      },
      mount(value) {
        context = value;
        const mounting = invoke('mount', raw);
        if (mounting !== undefined) return mounting;
        value.signal.addEventListener('abort', () => log.push('abort'),
          {once: true});
        if (options.moveMedia) for (const item of value.items)
          item.root.append(item.media);
        return {refresh() { return invoke('refresh', raw); },
          focus(id) { return invoke('focus', id); },
          key(event) {
            const result = invoke('key', event.key);
            if (result !== undefined) return result;
            if (options.ownerUndo && event.key === 'u') {
              if (callbacks.beforeChange() === false) return true;
              undoCalls++;
              if (options.undoFail) throw Error('fixture owner key failed');
              if (options.undoAsync)
                return Promise.reject(Error('fixture owner key pending'));
              raw = 'undone'; callbacks.changed(); return true;
            }
            if (options.invalidKey) return {};
            return options.consume?.includes(event.key) || false;
          },
          dispose() { disposed++; assert.equal(value.signal.aborted, true,
            'abort must precede dispose'); return invoke('dispose'); }};
      }};
    }}), 'Missing API2 single-view host contract');
  host.ReviewSheet.start();
  function key(value, target = active || body, extra = {}) {
    const event = {key: value, target, prevented: 0,
      preventDefault() { this.prevented++; }, ...extra};
    for (const callback of documentEvents.get('keydown') || []) callback(event);
    return event;
  }
  return {host, data, nodes, log, app, find, body, key, packet,
    context: () => context, raw: () => raw, stored: () => stored,
    edit(next, competingAfterPreflight) {
      if (factoryHost.beforeChange?.() === false) return false;
      mutations++; raw = next;
      if (competingAfterPreflight !== undefined)
        stored = competingAfterPreflight;
      factoryHost.changed(); return true;
    },
    setReviewer(value) {
      const input = find('reviewer'); input.value = value;
      input.events.get('input')[0]({target:input});
    },
    compete(value) { stored = value; },
    unreadable() { options.readFailure = true; },
    mutations: () => mutations, prepares: () => prepares,
    undoCalls: () => undoCalls, disposed: () => disposed,
    async import(value) {
      const text = JSON.stringify(value);
      await find('import-file').events.get('change')[0]({target: {files: [{
        size: text.length, async text() { return text; }}]}});
    },
    importFile(file) {
      return find('import-file').events.get('change')[0](
        {target: {files: [file]}});
    },
    async recoveryRaw() {
      nodes.find(node => node.textContent === 'Export recovery input').click();
      return [...exports.values()].at(-1).text();
    },
    pagehide() {
      for (const callback of windowEvents.get('pagehide') || []) callback();
    },
    async exportedText() {
      await find('export-results').click();
      return exports.size ? [...exports.values()].at(-1).text() : null;
    },
    async exported() {
      await find('export-results').click();
      return JSON.parse(await [...exports.values()].at(-1).text());
    },
    warnings: () => find('messages').children.map(node => node.textContent)};
}
function checkMediaStatus() {
  const source = fs.readFileSync(process.argv[2], 'utf8');
  const ready = source.indexOf('    const settleReady = () => {');
  const fail = source.indexOf('    const failMedia = message => {', ready);
  const api = source.indexOf('    const api = {', fail);
  const tail = source.indexOf('      if ((readyCalled && (!display ||', api);
  const finish = source.indexOf('    } catch (error) {', tail);
  assert(ready > 0 && fail > ready && api > fail && tail > api &&
    finish > tail);
  // Execute the shipped media callbacks, without simulating a browser decoder.
  function mediaFixture(scoped) {
    const calls = {status: [], resolved: [], rejected: [], errors: [],
      disposed: 0, terminated: 0, revoked: [], plays: 0, pauses: 0};
    const resources = {disposed: false, urls: ['blob:owned'], workers: [{
      terminate() { calls.terminated++; }}]};
    const stateKey = 'first:0';
    const mediaState = new Map([[stateKey, {src: 'owned.mp4', ready: false}]]);
    const env = {scopeKeys: scoped ? ['item:first:decision'] : [],
      failed: false, pageDisposed: false, resources, mediaState, stateKey,
      media: {src: 'owned.mp4', kind: 'video'}, readyCalled: false,
      handle: {resolve: value => calls.resolved.push(value),
        reject: error => calls.rejected.push(error.message)},
      api: {url: 'owned.mp4', bytes: null},
      display: {play() { calls.plays++; return 'playing'; },
        pause() { calls.pauses++; }},
      view: {}, component: {dispose() { calls.disposed++; }},
      shell: {append: node => calls.errors.push(node)},
      el: (tag, kind, text) => ({tag, kind, text}), failedMedia: new Set(),
      liveResources: new Set([resources]),
      URL: {revokeObjectURL: url => calls.revoked.push(url)},
      renderStatus() { calls.status.push(mediaState.get(stateKey).ready); }};
    vm.runInNewContext(source.slice(ready, api) +
      '\nglobalThis.callbacks = {settleReady, failMedia, finish() {\n' +
      source.slice(tail, finish) + '\n}};', env);
    return {env, calls, state: () => mediaState.get(stateKey)};
  }
  for (const scoped of [false, true]) {
    const f = mediaFixture(scoped), expected = Number(scoped);
    f.env.callbacks.settleReady();
    assert.equal(f.calls.status.length, expected,
      'Zero-scope media readiness must not scan primitive status');
    assert.equal(f.state().ready, true);
    assert.equal(f.calls.resolved.length, 1);
    assert(Object.isFrozen(f.calls.resolved[0]));
    assert.equal(f.calls.resolved[0].url, 'owned.mp4');
    assert.equal(f.calls.resolved[0].play(), 'playing');
    f.calls.resolved[0].pause();
    assert.equal(f.calls.plays, 1); assert.equal(f.calls.pauses, 1);
    f.env.callbacks.failMedia('actual fixture failure');
    assert.deepEqual(f.calls.status, scoped ? [true, false] : [],
      'Scoped media readiness/failure status transitions remain synchronous');
    assert.equal(f.state().ready, false);
    assert.deepEqual(f.calls.rejected, ['actual fixture failure']);
    assert.equal(f.calls.errors[0].text, 'actual fixture failure');
    assert.equal(f.calls.disposed, 1); assert.equal(f.calls.terminated, 1);
    assert.deepEqual(f.calls.revoked, ['blob:owned']);
    assert.equal(f.env.liveResources.size, 0);
    assert(f.env.failedMedia.has('first:0'));
    assert.throws(() => f.calls.resolved[0].play(), /media disposed/);
    f.env.callbacks.failMedia('duplicate'); f.env.callbacks.settleReady();
    assert.equal(f.calls.disposed, 1); assert.equal(f.calls.rejected.length, 1);
    assert.equal(f.calls.resolved.length, 1);
    const end = mediaFixture(scoped);
    end.env.callbacks.finish();
    assert.equal(end.calls.status.length, expected,
      'Zero-scope media render completion must not scan primitive status');
    assert.equal(end.calls.resolved.length, 0);
    end.env.mediaState.set('first:0', {src: 'owned.mp4', ready: true});
    end.env.callbacks.finish();
    assert.equal(end.calls.resolved.length, 1,
      'Render completion still settles an already-ready media handle');
    assert.equal(end.calls.status.length, 3 * expected);
  }
  const initial = fixture({saved: false});
  assert.equal(initial.find('progress').value, 0);
  assert.equal(initial.find('progress').max, 1);
  assert.equal(initial.find('progress-text').textContent,
    '0 of 0 required decisions answered', 'Initial empty status still renders');
}
async function main() {
  const name = process.argv[3];
  if (name === 'media-status') {
    checkMediaStatus(); console.log('PASS '+name); return;
  }
  if (name === 'messages-flow') {
    const f = fixture(), messages = f.find('messages');
    assert.equal(messages.parentNode, f.app,
      'Warnings must remain in normal app flow after sticky controls');
    assert.equal(f.app.children[0].tagName, 'HEADER');
    assert.equal(f.app.children[1], messages);
    assert.equal(f.app.children[0].querySelector('#messages'), null);
    const initial = f.warnings();
    assert.equal(initial.filter(text =>
      /Opaque project state carries/.test(text)).length, 1,
      'Startup queued warning must be flushed exactly once');
    const notes = Array.from({length: 9}, (_, index) =>
      'Retained alert '+index+' α <b>plain text</b>');
    notes.forEach((text, index) =>
      f.context().host.note(text, index % 2 === 0));
    assert.deepEqual(f.warnings(), [...initial, ...notes],
      'Every alert remains complete and ordered without caps or dismissal');
    assert.deepEqual(messages.children.slice(-9).map(node => node.className),
      notes.map((_, index) => 'banner'+(index % 2 === 0 ? ' error' : '')));
    assert.equal((await f.exported()).project_state.data, 'restored');
    assert.deepEqual(f.warnings().slice(0, initial.length+notes.length),
      [...initial, ...notes]);
    assert.equal(messages.parentNode, f.app);
    assert.equal(f.app.children[1], messages);
  } else if (name === 'reviewer-review') {
    for (const api of [2,1]) {
      const restored = fixture({api,savedReviewer:'Saved reviewer α'});
      assert.equal(restored.find('reviewer').value,'Saved reviewer α',
        'editor restore must preserve saved reviewer metadata');
      assert.equal(JSON.parse(restored.stored()).reviewer,'Saved reviewer α');
      assert.equal((await restored.exported()).reviewer,'Saved reviewer α');
      const imported = fixture({api,saved:false});
      await imported.import({...imported.packet('accepted'),
        reviewer:'Incoming reviewer'});
      assert.equal(imported.find('reviewer').value,'Incoming reviewer');
      assert.equal(JSON.parse(imported.stored()).reviewer,'Incoming reviewer');
      imported.setReviewer('Local reviewer');
      await imported.import({...imported.packet('second'),
        reviewer:'Do not replace local'});
      assert.equal((await imported.exported()).reviewer,'Local reviewer');
      await imported.import(imported.packet('third'));
      assert.equal((await imported.exported()).reviewer,'Local reviewer');
      const refused = fixture({api,saved:false,reject:'foreign'});
      await refused.import({...refused.packet('foreign state'),
        reviewer:'Refused reviewer'});
      assert.equal(refused.raw(),'baseline');
      assert.equal(refused.find('reviewer').value,'');
      assert.equal((await refused.exported()).reviewer,'');
      const growing = {api,saved:false,limit:1024};
      const bounded = fixture(growing), before = bounded.raw();
      growing.candidateSuffix = 'x'.repeat(1023-Buffer.byteLength(
        JSON.stringify({...bounded.packet('n'),context:{},reviewer:''}),
        'utf8'));
      const incoming = {...bounded.packet('n'),reviewer:'R'.repeat(200)};
      assert.ok(Buffer.byteLength(JSON.stringify(incoming),'utf8') < 1024);
      await bounded.import(incoming);
      assert.equal(bounded.raw(),before,
        'effective reviewer must be bounded before owner commit');
      assert.equal(bounded.find('reviewer').value,'');
      assert.equal((await bounded.exported()).reviewer,'');
      assert.equal(await bounded.recoveryRaw(),JSON.stringify(incoming));
    }
  } else if (name === 'navigation-review') {
    const f = fixture({groups:true}), api = f.context().host;
    api.setVisible(['first','third']); api.select('first',false);
    f.key('J');
    assert.equal(api.currentId(),'third',
      'group navigation must select a visible group member');
    f.key('K'); assert.equal(api.currentId(),'first');
    api.setVisible(['first']); f.key('J');
    assert.equal(api.currentId(),'first',
      'all-hidden groups must not gain focus');
    api.setVisible([]); f.key('J'); f.key('K');
    assert.equal(api.currentId(),null,'empty visible set keeps null focus');
    const filtered = fixture({query:'Second'}), host = filtered.context().host;
    filtered.key('/');
    assert.equal(host.currentId(),'second',
      'filtering current must reconcile focus to an actually visible item');
    const none = fixture({query:'nothing'}); none.key('/');
    assert.equal(none.context().host.currentId(),null);
  } else if (name === 'export-limit-review') {
    const downloads = [];
    const binding = process.argv[4] ? JSON.parse(process.argv[4]) : undefined;
    for (const api of binding ? [binding.api] : [1,2])
      for (const rawPrefix of ['', 'α🦉 \\u0061 "\r\n']) {
      const f = fixture({api,limit:1024,saved:false,rawPrefix,binding});
      if (binding) {
        const overhead = Buffer.byteLength(JSON.stringify({
          ...f.packet(rawPrefix),
          context:f.data.description.context || {},reviewer:''}),'utf8');
        const candidate = rawPrefix+'x'.repeat(1024-overhead);
        await f.import(f.packet(candidate));
        assert.equal(f.raw(),candidate,
          'actual transport admits full-bound candidate');
      }
      const accepted = f.raw();
      assert.equal(Buffer.byteLength(JSON.stringify({...f.packet(accepted),
        context:f.data.description.context || {},reviewer:''}),'utf8'),1024);
      const text = await f.exportedText();
      assert.notEqual(text,null,
        'an admitted exact-limit editor state must remain exportable');
      assert.equal(Buffer.byteLength(text,'utf8'),1024);
      assert.equal(JSON.parse(text).project_state.data,f.raw());
      downloads.push(text);
      const overflow = f.packet(f.raw()+'x'); await f.import(overflow);
      assert.equal(f.raw(),accepted,
        'one-byte oversized input cannot mutate a valid exact-limit state');
    }
    if (binding) console.log('DOWNLOAD '+JSON.stringify(downloads));
  } else if (name === 'undo-review') {
    for (const api of [1,2]) {
      const idle = fixture({api,undoFail:true});
      assert.doesNotThrow(() => idle.key('u'),
        'editor default must not invoke an undeclared undo hook');
      assert.equal(idle.undoCalls(),0);
      assert.equal(idle.raw(),'restored');
    }
    const owned = fixture({ownerUndo:true}); owned.key('u');
    assert.equal(owned.undoCalls(),1,'owner key retains domain undo');
    assert.equal(owned.raw(),'undone');
    const competing = fixture({ownerUndo:true}); competing.compete('other tab');
    competing.key('u'); assert.equal(competing.undoCalls(),0);
    assert.equal(competing.raw(),'restored',
      'owner undo checks beforeChange first');
    const unhandled = [], observe = reason => unhandled.push(String(reason));
    process.on('unhandledRejection',observe);
    try {
      for (const options of [{undoFail:true},{undoAsync:true}]) {
        const f = fixture({...options,ownerUndo:true});
        assert.doesNotThrow(() => f.key('u'));
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(f.context().signal.aborted,true);
        assert.ok(f.warnings().some(text =>
          /view.*failed|view.*unavailable/i.test(text)));
        assert.equal((await f.exported()).project_state.data,'restored');
      }
      assert.deepEqual(unhandled,[],'owner key rejection must be handled');
    } finally {process.removeListener('unhandledRejection',observe);}
  } else if (name === 'labels-review') {
    const f = fixture();
    assert.match(f.find('export-results').textContent,/save editor state/i,
      'generic envelope action must be distinct from canonical feedback ' +
      'download');
    assert.ok(f.nodes.some(node =>
      /import editor state/i.test(node.textContent)));
    const labels = f.nodes.map(node => node.textContent);
    assert.ok(labels.includes('Discard recovery input'),
      'the discard control must say what it discards on an editor page');
    assert.ok(!labels.includes('Discard stale draft entries'));
  } else if (name === 'mount') {
    const f = fixture({moveMedia: true}), context = f.context();
    assert.ok(context, 'API2 must mount exactly one explicit owner view');
    assert.equal(f.log.filter(v => v[0] === 'mount').length, 1);
    assert.equal(f.log.find(v => v[0] === 'mount')[1], 'restored',
      'saved owner state must be restored before mounting');
    assert.equal(context.items.map(v => v.id).join(','), 'first,second');
    assert.ok(Object.isFrozen(context.items));
    assert.notEqual(context.root, f.app);
    for (const item of context.items) {
      assert.ok(Object.isFrozen(item));
      assert.notEqual(item.root, item.media);
      assert.equal(item.media.parentNode, item.root,
        'explicit host media container may be moved within owner root');
      const wrapper = f.nodes.find(node => node.dataset.itemId === item.id);
      assert.ok(wrapper.querySelectorAll('div').includes(item.root));
      assert.equal(wrapper.dataset.itemId, item.id);
    }
    assert.ok(f.log.findIndex(v => v[0] === 'refresh') >
      f.log.findIndex(v => v[0] === 'mount'));
    assert.equal(context.host.currentId(), 'first');
  } else if (name === 'key') {
    const f = fixture({consume: ['n', 'u', 'Escape', '?']});
    const count = () => f.log.filter(v => v[0] === 'key').length;
    assert.equal(f.key('n').prevented, 1);
    assert.equal(f.context().host.currentId(), 'first');
    assert.equal(f.key('u').prevented, 1);
    assert.equal(f.undoCalls(), 0,
      'consumed owner key must not reach host undo');
    const input = f.host.document.createElement('textarea'); input.focus();
    const before = count(); f.key('n', input);
    assert.equal(count(), before, 'typing must not reach owner shortcuts');
    assert.equal(f.key('Escape', input).prevented, 1);
    f.body.focus(); // Following modifier/punctuation cases are not text input.
    assert.equal(f.host.document.activeElement, f.body);
    const after = count();
    for (const modifier of ['ctrlKey', 'metaKey', 'altKey']) {
      f.key('n', f.body, {[modifier]: true});
      assert.equal(count(), after, modifier+' must suppress owner shortcuts');
    }
    f.key('?', f.body, {shiftKey: true});
    assert.equal(count(), after + 1,
      'Shift punctuation retains legacy dispatch');
    f.key('j', f.body);
    assert.equal(f.context().host.currentId(), 'second',
      'unhandled key falls back');
    assert.equal(f.log.filter(v => v[0] === 'key' && v[1] === 'j').length, 1);
  } else if (name === 'focus') {
    const f = fixture(), host = f.context().host;
    host.select('second', false);
    assert.equal(host.currentId(), 'second');
    assert.deepEqual(f.log.at(-1), ['focus', 'second']);
    assert.throws(() => host.select('hidden-history'), /item|bound|visible/i);
    assert.equal(host.currentId(), 'second');
    for (const ids of [['second', 'second'], ['hidden-history']]) {
      assert.throws(() => host.setVisible(ids), /item|bound|duplicate/i);
      assert.equal(host.currentId(), 'second',
        'invalid subset cannot move focus');
      for (const id of ['first', 'second']) {
        const wrapper = f.nodes.find(node => node.dataset.itemId === id);
        assert.equal(
          wrapper.hidden || wrapper.classList.contains('hidden'), false,
          'invalid subset cannot partially hide a previously visible item');
        host.select(id, false);
        assert.equal(host.currentId(), id,
          'previously visible item remains selectable');
      }
    }
    host.setVisible(['second']);
    assert.throws(() => host.select('first'), /visible/i);
    host.setVisible([]);
    assert.equal(host.currentId(), null);
    assert.deepEqual(f.log.at(-1), ['focus', null]);
    host.setVisible(['first', 'second']); host.select('first', false);
    assert.equal(host.currentId(), 'first');
  } else if (name === 'failure') {
    const unhandled = [], observe = reason => unhandled.push(String(reason));
    process.on('unhandledRejection', observe);
    try {
      for (const hook of ['mount', 'refresh', 'focus', 'key'])
        for (const kind of ['fail', 'async']) {
          const f = fixture({[kind]: hook});
          if (hook === 'key') f.key('n');
          await new Promise(resolve => setImmediate(resolve));
          assert.equal(f.context().signal.aborted, true, hook);
          assert.ok(f.warnings().some(text =>
            /view.*failed|view.*unavailable/i.test(text)),
            'visual failure must remain visible');
          assert.notEqual(f.find('export-results').disabled, true,
            'valid bound state remains exportable despite visual failure');
          const packet = await f.exported();
          assert.equal(packet.project_state.data, 'restored');
          assert.equal(f.context().host.projectState(), 'restored',
            'visual failure does not corrupt validated canonical export');
          assert.equal(f.raw(), 'restored',
            'visual failure cannot invent state rollback');
          assert.ok(f.warnings().some(text => /unverified|recover/i.test(text)),
            'visual failure export must be explicitly unverified/recovery');
          const before = f.log.length; f.key('n');
          assert.equal(f.log.length, before,
            'failed view receives no further keys');
        }
      const invalid = fixture({invalidKey: true});
      invalid.key('n');
      assert.equal(invalid.context().signal.aborted, true,
        'key must return a boolean, not a truthy object');
      assert.equal((await invalid.exported()).project_state.data, 'restored');
      assert.deepEqual(unhandled, [], 'async hook rejection must be handled');
    } finally { process.removeListener('unhandledRejection', observe); }
  } else if (name === 'dispose') {
    const unhandled = [], observe = reason => unhandled.push(String(reason));
    process.on('unhandledRejection', observe);
    try {
    for (const options of [{}, {fail: 'dispose'}, {async: 'dispose'}]) {
      const f = fixture(options);
      f.pagehide(); f.pagehide();
      assert.equal(f.disposed(), 1);
      assert.equal(f.context().signal.aborted, true);
      assert.equal(f.context().host.canImport, false,
        'disposed view cannot import');
      assert.ok(f.log.indexOf('abort') <
        f.log.findIndex(v => v[0] === 'dispose'));
      const before = f.log.length; f.key('n');
      assert.equal(f.log.length, before, 'disposed view is inactive');
      await new Promise(resolve => setImmediate(resolve));
      if (options.fail || options.async) assert.ok(f.warnings().some(
        text => /dispose|view.*failed/i.test(text)));
    }
    assert.deepEqual(unhandled, [], 'dispose rejection must be handled');
    } finally { process.removeListener('unhandledRejection', observe); }
  } else if (name === 'refresh') {
    const f = fixture({ownerUndo:true});
    f.edit('edited');
    assert.deepEqual(f.log.at(-1), ['refresh', 'edited']);
    assert.equal(JSON.parse(f.stored()).project_state.data, 'edited');
    assert.equal(f.context().host.projectState(), 'edited',
      'canonical download reads validated current raw after an edit');
    await f.import(f.packet('imported'));
    assert.deepEqual(f.log.at(-1), ['refresh', 'imported']);
    assert.equal(f.context().host.projectState(), 'imported');
    f.key('u');
    assert.equal(f.undoCalls(), 1);
    assert.ok(f.log.some(v => v[0] === 'refresh' && v[1] === 'undone'));
    assert.equal(f.context().host.projectState(), 'undone');
    await f.import({...f.packet('foreign'), resolved_sha256: '0'.repeat(64)});
    assert.equal(f.raw(), 'undone');
    assert.deepEqual(f.log.at(-1), ['refresh', 'undone']);
    const exact = ' {"kept":"\\u0061","value":false}\r\n';
    const accepted = fixture(), api = accepted.context().host;
    assert.equal(api.canImport, true);
    assert.equal(typeof Object.getOwnPropertyDescriptor(api, 'canImport').get,
      'function');
    assert.equal(Reflect.set(api, 'canImport', false), false,
      'view cannot override the recovery barrier');
    assert.equal(api.importProject(exact).ok, true);
    assert.equal(api.projectState(), exact,
      'canonical packet text is never normalized');
    assert.deepEqual(accepted.log.at(-1), ['refresh', exact]);
    assert.equal(JSON.parse(accepted.stored()).project_state.data, exact);
    for (const value of [' foreign canonical \\u0061\r\n', 'x'.repeat(16385)]) {
      const refused = fixture({reject: 'foreign'});
      const api = refused.context().host;
      const before = refused.raw(), stored = refused.stored();
      assert.equal(api.importProject(value).ok, false);
      assert.equal(refused.raw(), before,
        'invalid canonical input cannot swap owner');
      assert.equal(refused.stored(), stored);
      assert.equal(await refused.recoveryRaw(), value,
        'recovery retains original canonical raw, not a synthetic wrapper');
      assert.equal(api.canImport, false);
      let reads = 0;
      await refused.importFile({size: 3,
        async text() { reads++; return '{}'; }});
      assert.equal(reads, 0,
        'pending recovery refuses before reading another file');
      assert.equal(api.importProject('second canonical input').consumed, false);
      assert.equal(await refused.recoveryRaw(), value);
    }
    const concurrent = fixture({reject: 'foreign'});
    let release, reads = 0;
    const pending = concurrent.importFile({size: 10, text() {
      reads++; return new Promise(resolve => { release = resolve; });
    }});
    assert.equal(reads, 1);
    const firstRaw = 'foreign during awaited file read \\u0061';
    concurrent.context().host.importProject(firstRaw);
    release(JSON.stringify(concurrent.packet('late input')));
    await pending;
    assert.equal(concurrent.raw(), 'restored');
    assert.equal(await concurrent.recoveryRaw(), firstRaw,
      'awaited file completion cannot replace a now-pending recovery');
    const broken = fixture({saved: false, unrecovered: true});
    const brokenApi = broken.context().host;
    const brokenRaw = 'raw failed after swap';
    assert.equal(brokenApi.importProject(brokenRaw).unrecovered, true);
    assert.throws(() => brokenApi.projectState(), /unrecovered/i,
      'direct canonical download must refuse actual transport corruption');
    assert.equal(brokenApi.canImport, false);
    assert.equal(broken.find('export-results').disabled, true);
    assert.equal(await broken.recoveryRaw(), brokenRaw);
    assert.equal(broken.raw(), brokenRaw,
      'rollback failure cannot claim restoration');
  } else if (name === 'concurrency') {
    for (const cause of ['changed', 'unreadable']) {
      const f = fixture(), api = f.context().host;
      const old = f.stored();
      const competing = ' '+JSON.stringify(f.packet('newer other tab'))+'\n';
      if (cause === 'changed') f.compete(competing); else f.unreadable();
      assert.equal(f.edit('must not become an owner event'), false,
        'API2 guard must refuse before cooperative owner mutation');
      assert.equal(f.mutations(), 0);
      assert.equal(f.raw(), 'restored');
      assert.equal(f.stored(), cause === 'changed' ? competing : old);
      assert.equal(api.projectState(), 'restored');
      const prepared = f.prepares();
      assert.equal(api.importProject('must not prepare').consumed, false);
      assert.equal(f.prepares(), prepared,
        'draft guard runs before owner prepare');
      f.key('u');
      assert.equal(f.undoCalls(), 0,
        'default undo also checks before owner mutation');
      assert.ok(f.warnings().some(text =>
        /draft.*changed|reconcil|unreadable|unavailable/i.test(text)));
      assert.equal((await f.exported()).project_state.data, 'restored');
    }
    const race = fixture();
    const competing = JSON.stringify(race.packet('other tab wins'));
    assert.equal(race.edit('valid local event', competing), true,
      'the precheck cannot claim atomic WebStorage compare-and-swap');
    assert.equal(race.mutations(), 1);
    assert.equal(race.raw(), 'valid local event', 'no invented owner rollback');
    assert.equal(race.stored(), competing,
      'postcheck refuses to overwrite newer raw');
    assert.equal(race.context().host.projectState(), 'valid local event');
    assert.equal((await race.exported()).project_state.data,
      'valid local event');
    assert.ok(race.warnings().some(text =>
      /reconcil|draft.*changed/i.test(text)));
    assert.equal(race.edit('blocked subsequent event'), false);
    assert.equal(race.mutations(), 1);
  } else throw Error('Unknown permanent view contract: '+name);
  console.log('PASS '+name);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
