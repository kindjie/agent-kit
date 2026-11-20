/* Pure shipped transport contracts; no browser or project domain knowledge. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = {TextEncoder, TextDecoder, structuredClone};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
assert.equal(typeof context.ReviewSheetEditorTransport?.create, 'function',
  'Missing owning-editor transport API');
const Transport = context.ReviewSheetEditorTransport;
const digest = 'a'.repeat(64);
function envelope(data) {
  return {schema_version: 1, editor_sha256: digest, data};
}
function fixture(mode) {
  let current, committed = 0, rolledBack = 0;
  let stored = 'ORIGINAL DRAFT BYTES \\u0061';
  const binding = {sha256: digest, initial_state: 'before', parameters: {},
    source_identity: 'fixture:source', visible_item_ids: ['visible'],
    bundle: {sha256: 'b'.repeat(64)}, kind: 'fixture-editor',
    version: '1.0.0', api: 1};
  const transport = Transport.create({binding, limit: 4096,
    create(config, host) {
      current = {raw: config.initial_state, undo: ['old-event'],
        held: '{"raw":"\\u0061"}'};
      return {snapshot: () => current.raw, prepare(raw) {
        const old = current;
        const next = {...current, raw, undo: [...current.undo, 'new-event']};
        if (mode === 'prepare-throw') throw Error('fixture prepare failure');
        const txn = {
          snapshot: () => mode === 'oversize' ? 'x'.repeat(4097) : raw,
          commit() {
          if (mode === 'stale') throw Error('fixture stale transaction');
          committed++;
          current = next;
          if (mode === 'notify') host.changed();
          if (mode === 'async-commit') return Promise.resolve().then(() => {
            current = next;
            throw Error('fixture pending commit failure');
          });
          if (['commit', 'rollback', 'notify', 'async-rollback'].includes(mode))
            throw Error('fixture failed after state swap');
        }, rollback() {
          rolledBack++;
          if (mode === 'rollback') throw Error('fixture rollback failure');
          if (mode === 'async-rollback') return Promise.reject(
            Error('fixture pending rollback failure'));
          current = old;
        }};
        if (mode === 'missing-rollback') delete txn.rollback;
        if (mode === 'promise') return Promise.resolve(txn);
        return txn;
      }};
    }, persist(value) {
      if (mode === 'persistence') throw Error('fixture quota failure');
      stored = JSON.stringify(value);
    }});
  return {transport, binding, state: () => structuredClone(current),
    writes: () => committed, rollbacks: () => rolledBack,
    stored: () => stored};
}
function notifyContract() {
  const f = fixture('notify'), original = f.stored(), before = f.state();
  const result = f.transport.import(envelope('next'), 'original raw notify');
  assert.equal(result.ok, false);
  assert.deepEqual(f.state(), before);
  assert.equal(f.stored(), original,
    'changed callback must not persist in-flight');
}
function bindingContract() {
  const f = fixture('valid');
  f.binding.sha256 = '0'.repeat(64);
  assert.equal(f.transport.envelope().editor_sha256, digest,
    'caller mutation must not retag frozen state');
}
function cliBindingContract() {
  // A binding emitted by the CLI into a page: the owner factory receives it
  // unchanged, and nothing in it locates files on the resolving machine.
  const binding = JSON.parse(process.argv[4]);
  const local = JSON.parse(process.argv[5]);
  assert.ok(Array.isArray(local) && local.length > 0);
  let config;
  Transport.create({binding, limit: 1 << 20, create(value) {
    config = value;
    let raw = value.initial_state;
    return {snapshot: () => raw, prepare(next) {
      const old = raw;
      return {snapshot: () => next, commit() { raw = next; },
        rollback() { raw = old; }};
    }};
  }});
  const text = JSON.stringify(config);
  assert.equal(text, JSON.stringify(binding),
    'factory config must be the emitted binding');
  assert.deepEqual(Object.keys(config.bundle).sort(),
    ['api', 'kind', 'sha256', 'version']);
  for (const path of local)
    assert.equal(text.includes(path), false,
      'factory config must not carry a local filesystem path');
}
function bindingSnapshotContract() {
  // Count real full-config snapshots, not RSS or engine string backing stores.
  // The ordinary fixture/domain below is unchanged by this structural budget.
  const originalClone=context.structuredClone;
  try {
    for(const api of [1,2]) {
      const binding={sha256:digest,api,initial_state:'before',
        source_identity:'fixture:source',visible_item_ids:['visible'],
        parameters:{ui:{cards:{
          visible:'complete owner card '+ 'x'.repeat(8192)}},
          provenance:{kept:[false,0,'']}},bundle:{sha256:'b'.repeat(64)}};
      let snapshots=0,config,current;
      context.structuredClone=value=>{
        if(value?.parameters?.ui) snapshots++;
        return structuredClone(value);
      };
      const transport=Transport.create({binding,limit:4096,
        create(received) {
          config=received;current=received.initial_state;
          // The factory may mutate its own copied config immediately.
          config.sha256='f'.repeat(64);
          config.parameters.ui.cards.visible='factory card';
          config.parameters.provenance.kept.push('factory-only');
          return {snapshot:()=>current,prepare(next) {
            const before=current;
            return {snapshot:()=>next,commit(){current=next;},
              rollback(){current=before;}};
          }};
        }});
      assert.notEqual(config,binding);
      assert.notEqual(config.parameters,binding.parameters);
      assert.notEqual(config.parameters.ui,binding.parameters.ui);
      assert.equal(binding.sha256,digest,
        'factory mutation cannot change caller');
      assert.match(binding.parameters.ui.cards.visible,/^complete owner card/);
      assert.deepEqual(binding.parameters.provenance.kept,[false,0,'']);
      assert.equal(transport.envelope().editor_sha256,digest,
        'SHA must be captured before factory mutation');
      binding.sha256='0'.repeat(64);
      binding.parameters.ui.cards.visible='caller later card';
      binding.visible_item_ids.push('caller-only');
      assert.equal(config.parameters.ui.cards.visible,'factory card');
      assert.deepEqual(config.visible_item_ids,['visible']);
      assert.equal(transport.envelope().editor_sha256,digest,
        'caller mutation must not retag exported state');
      assert.equal(
        transport.import(envelope('after'),'original raw input').ok,true);
      assert.equal(transport.envelope().data,'after');
      assert.equal(transport.envelope().editor_sha256,digest);
      assert.equal(snapshots,1,
        'transport must snapshot the full factory config exactly once');
    }
  } finally {context.structuredClone=originalClone;}
}
async function asyncContract() {
  const unhandled = [];
  const observe = reason => unhandled.push(String(reason));
  process.on('unhandledRejection', observe);
  try {
    for (const mode of ['async-commit', 'async-rollback']) {
      const f = fixture(mode), original = f.stored();
      const result = f.transport.import(envelope('next'), 'raw '+mode);
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(result.ok, false, 'thenable lifecycle cannot succeed');
      assert.equal(result.unrecovered, true,
        'pending work cannot assure rollback');
      assert.equal(f.stored(), original);
      assert.equal(f.transport.recovery.raw, 'raw '+mode);
      assert.throws(() => f.transport.envelope(), /unrecovered/i);
      assert.deepEqual(unhandled, [], 'thenable rejection must be handled');
    }
  } finally { process.removeListener('unhandledRejection', observe); }
}
async function hostContract(mode, override) {
  const variants = mode === 'headers' ? [{answers: true}, {answers: 1},
    {context: []}, {context: null}, {reviewer: {}}] : mode === 'startup' ?
    [{stale: true}, {readFail: true}, {quota: true}] : mode === 'ordinary' ?
    [{ordinary: true}] : mode === 'unrecovered' ? [{unrecovered: true}] :
    [{}, {stale: true}];
  for (const variant of variants) {
    const nodes = [];
    function element(tag) {
      const node = {tag, children: [], events: {}, dataset: {}, value: '',
        append(...children) { this.children.push(...children); },
        addEventListener(name, callback) { this.events[name] = callback; },
        setAttribute() {}, click() { this.events.click?.(); }};
      nodes.push(node); return node;
    }
    const app = element('main'); app.id = 'app';
    const find = id => nodes.find(node => node.id === id);
    const data = {description: {review: 'host-fixture', items: []}, scopes: {},
      components: {}, limits: {results: 4096}, resolved_sha256: digest,
      editor: {sha256: digest, initial_state: 'before', parameters: {},
        source_identity: 'host:fixture', visible_item_ids: [],
        kind: 'fixture-editor', version: '1.0.0', api: 1}};
    const packet = {schema_version: 2, review: 'host-fixture',
      resolved_sha256: variant.stale ? '0'.repeat(64) : digest, answers: {},
      project_state: envelope('restored')};
    for (const key of ['answers', 'context', 'reviewer'])
      if (key in variant) packet[key] = variant[key];
    if (variant.ordinary) delete data.editor;
    const draft = variant.ordinary ? ' '+JSON.stringify({schema_version: 1,
      review: 'host-fixture', reviewer: '', answers: {}, reveals: {}})+'\n' :
      JSON.stringify(packet);
    let stored = draft, ownerRaw;
    const host = {TextEncoder, TextDecoder, structuredClone,
      REVIEW_SHEET_DATA: data, ReviewSheetEditorTransport: Transport,
      setTimeout: callback => setTimeout(callback, 1),
      localStorage: {getItem() {
        if (variant.readFail) throw Error('fixture unreadable storage');
        return stored;
      }, setItem(_, raw) {
        if (variant.quota) throw Error('fixture storage quota');
        stored = raw;
      }},
      document: {createElement: element, getElementById: find,
        querySelector: key => key.startsWith('#') ? find(key.slice(1)) : null,
        querySelectorAll: () => [], addEventListener() {}},
      addEventListener() {}};
    host.window = host;
    const page = override || require('node:path').join(
      require('node:path').dirname(process.argv[2]), 'review_sheet_page.js');
    vm.runInNewContext(fs.readFileSync(page, 'utf8'), host);
    if (!variant.ordinary) host.ReviewSheet.registerEditor({
      kind: 'fixture-editor', version: '1.0.0', api: 1, create(config) {
        ownerRaw = config.initial_state;
        return {snapshot: () => ownerRaw, prepare(next) {
          const old = ownerRaw;
          return {snapshot: () => next, commit() {
            ownerRaw = next;
            if (variant.unrecovered) throw Error('fixture failed after swap');
          }, rollback() {
            if (variant.unrecovered) throw Error('fixture rollback failed');
            ownerRaw = old;
          }};
        }};
      }});
    host.ReviewSheet.start();
    const message = () => find('messages').children.at(-1).textContent;
    const labels = nodes.map(node => node.textContent);
    assert.equal(labels.includes('Discard recovery input'), !variant.ordinary,
      'only an editor page offers to discard its recovery input');
    assert.equal(labels.includes('Discard stale draft entries'),
      !!variant.ordinary, 'only an ordinary page discards stale entries');
    if (variant.unrecovered) {
      assert.match(message(), /unrecovered/i,
        'page must expose unrecovered editor state');
      assert.equal(ownerRaw, 'restored',
        'failed rollback left changed owner state');
      assert.equal(find('export-results').disabled, true);
      await find('export-results').events.click();
      assert.match(message(), /export refused.*unrecovered/i);
      nodes.find(node => node.textContent === 'Discard recovery input')
        .events.click();
      assert.match(message(), /unrecovered/i);
      assert.equal(stored, draft,
        'normal export/discard cannot hide failed rollback');
      continue;
    }
    if (mode === 'startup')
      assert.match(message(), /refused|failed|unavailable/i,
        'startup must leave the actionable failure notice last');
    if (mode === 'headers') {
      assert.equal(ownerRaw, 'before',
        'browser rejects malformed editor header before owner mutation');
      assert.equal(stored, draft);
      continue;
    }
    if (variant.ordinary) {
      const raw = JSON.stringify({...packet, schema_version: 1});
      await find('import-file').events.change({target: {files: [{
        size: raw.length, async text() { return raw; }}]}});
      assert.match(message(), /project state.*v2/i,
        'ordinary v1 must refuse project state');
      assert.equal(stored, draft);
      continue;
    }
    find('reviewer').value = 'fixture reviewer';
    find('reviewer').events.input();
    if (variant.stale || variant.readFail || variant.quota) {
      assert.equal(stored, draft,
        'page retains original failed/unreadable draft');
      if (variant.stale || variant.quota) {
        let reads = 0;
        await find('import-file').events.change({target: {files: [{size: 1,
          async text() { reads++; return 'second raw input'; }}]}});
        assert.equal(reads, 0,
          'page must not consume another pending recovery input');
      }
    } else {
      const saved = JSON.parse(stored);
      assert.equal(saved.schema_version, 2,
        'page never writes a v1 editor draft');
      assert.deepEqual(saved.answers, {});
      assert.equal(saved.project_state.data, 'restored');
    }
  }
}
async function main() {
  const name = process.argv[3];
  if (name === 'notify') { notifyContract(); return; }
  if (name === 'binding') { bindingContract(); return; }
  if (name === 'binding-snapshot') { bindingSnapshotContract(); return; }
  if (name === 'cli-binding') { cliBindingContract(); return; }
  if (name === 'async') { await asyncContract(); return; }
  if (['startup', 'headers', 'ordinary', 'unrecovered'].includes(name)) {
    await hostContract(name, process.argv[4]); return;
  }
  if (name === 'commit') {
    notifyContract();
    const f = fixture('commit'), before = f.state(), store = f.stored();
    const raw = ' {"original":"raw \\u0061"} \n';
    const result = f.transport.import(envelope('next'), raw);
    assert.equal(result.ok, false);
    assert.deepEqual(f.state(), before,
      'full state, undo and held text restored');
    assert.equal(f.stored(), store, 'original draft bytes never overwritten');
    assert.equal(f.transport.recovery.raw, raw);
    assert.equal(f.writes(), 1, 'failure occurred after the actual state swap');
    assert.equal(f.rollbacks(), 1);
    assert.equal(f.transport.status.unrecovered, false);
    const second = f.transport.import(envelope('later'), 'second raw input');
    assert.equal(second.ok, false, 'pending recovery prevents another import');
    assert.equal(second.consumed, false);
    assert.equal(f.transport.recovery.raw, raw,
      'first recovery is not overwritten');
    assert.deepEqual(f.state(), before);
  } else if (name === 'rollback') {
    const f = fixture('rollback'), raw = '{"keep":"rejected source"}';
    const result = f.transport.import(envelope('next'), raw);
    assert.equal(result.ok, false);
    assert.equal(result.unrecovered, true);
    assert.equal(f.state().raw, 'next',
      'failed rollback must not claim restoration');
    assert.equal(f.transport.status.unrecovered, true);
    assert.equal(f.transport.recovery.raw, raw);
    assert.throws(() => f.transport.envelope(), /unrecovered/i);
  } else if (name === 'lifecycle') {
    bindingContract();
    await asyncContract();
    for (const mode of ['missing-rollback', 'promise', 'prepare-throw',
      'stale', 'oversize']) {
      const f = fixture(mode), before = f.state(), stored = f.stored();
      const result = f.transport.import(envelope('next'), 'raw-'+mode);
      assert.equal(result.ok, false, mode);
      assert.equal(f.writes(), 0, mode+' must reject before state swap');
      assert.deepEqual(f.state(), before, mode);
      assert.equal(f.stored(), stored, mode);
      assert.equal(f.transport.recovery.raw, 'raw-'+mode);
    }
    const f = fixture('valid'), before = f.state();
    for (const changed of [
      {schema_version: 9}, {editor_sha256: '0'.repeat(64)},
      {data: 'x'.repeat(4097)}, {data: '\ud800'}, {data: []},
      {unknown: true}]) {
      const next = {...envelope('next'), ...changed};
      const one = fixture('valid');
      const result = one.transport.import(next, 'raw invalid envelope');
      assert.equal(result.ok, false);
      assert.deepEqual(one.state(), before);
      assert.equal(one.writes(), 0);
    }
    assert.deepEqual(f.state(), before);
  } else if (name === 'persistence') {
    const f = fixture('persistence'), stored = f.stored();
    const raw = ' {"exact":"accepted source \\u0061"} ';
    const result = f.transport.import(envelope('accepted'), raw);
    assert.equal(result.ok, true, 'owner transition succeeded in memory');
    assert.equal(result.persisted, false,
      'storage failure must remain explicit');
    assert.equal(f.transport.envelope().data, 'accepted');
    assert.equal(f.stored(), stored,
      'quota failure keeps original draft bytes');
    assert.equal(f.transport.recovery.raw, raw);
    assert.equal(f.transport.status.storageSafe, false);
    // Shipped page glue in a tiny DOM fixture, not browser/media evidence.
    for (const mode of ['normal', 'startup', 'headers', 'ordinary',
      'unrecovered'])
      await hostContract(mode);
  } else throw Error('Unknown permanent contract case: '+name);
  console.log('PASS '+name);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
