/* Opaque state transport. Project validation and edits belong to the owner. */
(() => {
  'use strict';
  const pendingWork = value => {
    if (!value || typeof value.then !== 'function') return false;
    Promise.resolve(value).catch(() => {});
    return true;
  };
  const bytes = text => {
    if (pendingWork(text) || typeof text !== 'string' || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text))
      throw Error('State must be well-formed UTF-8 text');
    return new TextEncoder().encode(text).length;
  };
  function create({binding: input, limit, create: factory, persist, wrap = v => v}) {
    const factoryConfig = structuredClone(input);
    const bindingSha256 = factoryConfig.sha256;
    let recovery = null;
    let inFlight = true;
    const status = {unrecovered: false, storageSafe: true};
    const session = factory(factoryConfig, {changed() { return save(); }});
    if (pendingWork(session) || !session || typeof session.snapshot !== 'function' ||
        typeof session.prepare !== 'function') throw Error('Invalid editor session');
    function envelope(raw = session.snapshot()) {
      if (status.unrecovered) throw Error('Editor state is unrecovered');
      if (bytes(raw) > limit) throw Error('Project state exceeds results limit');
      const value = {schema_version: 1, editor_sha256: bindingSha256, data: raw};
      if (bytes(JSON.stringify(wrap(value))) > limit)
        throw Error('Complete results exceed size limit');
      return value;
    }
    envelope(); // Refuse a malformed/oversize baseline before accepting imports.
    inFlight = false;
    function save() {
      if (inFlight || recovery || status.unrecovered) return false;
      try { persist?.(wrap(envelope())); return true; }
      catch (_) { status.storageSafe = false; return false; }
    }
    function reject(raw, error, unrecovered = false) {
      recovery = {raw, error: String(error)};
      status.unrecovered ||= unrecovered;
      return {ok: false, consumed: true, unrecovered: status.unrecovered};
    }
    return {session, status, envelope, save,
      get canImport() { return !inFlight && !recovery && !status.unrecovered; },
      get recovery() { return recovery; },
      discardRecovery() {
        if (status.unrecovered) throw Error('Cannot discard unrecovered state');
        recovery = null;
      },
      import(value, raw) {
        if (inFlight || recovery || status.unrecovered)
          return {ok: false, consumed: false};
        let txn, before, pending = false;
        function synchronous(value) {
          if (pendingWork(value)) {
            pending = true;
            throw Error('Asynchronous editor lifecycle refused');
          }
        }
        inFlight = true;
        try {
          if (bytes(raw) > limit) throw Error('Import exceeds size limit');
          if (!value || Object.keys(value).sort().join(',') !==
              'data,editor_sha256,schema_version' || value.schema_version !== 1 ||
              value.editor_sha256 !== bindingSha256)
            throw Error('Wrong editor binding or malformed envelope');
          envelope(value.data);
          before = session.snapshot();
          synchronous(before);
          txn = session.prepare(value.data);
          synchronous(txn);
          if (!txn ||
              ['snapshot', 'commit', 'rollback'].some(k =>
                typeof txn[k] !== 'function')) throw Error('Invalid transaction');
          const candidate = txn.snapshot();
          synchronous(candidate);
          envelope(candidate); // Bound the full export before owner writes.
          synchronous(txn.commit());
          const committed = session.snapshot();
          synchronous(committed);
          if (committed !== candidate)
            throw Error('Committed state differs from prepared candidate');
          const accepted = envelope();
          try { persist?.(wrap(accepted)); }
          catch (error) {
            status.storageSafe = false;
            recovery = {raw, error: String(error)};
            return {ok: true, persisted: false};
          }
          status.storageSafe = true;
          return {ok: true, persisted: true};
        } catch (error) {
          let unrecovered = pending;
          // A trusted owner may fail after swapping; rollback is obligatory.
          if (txn && typeof txn.rollback === 'function') {
            try { synchronous(txn.rollback()); }
            catch (_) { unrecovered = true; }
          }
          try {
            const restored = session.snapshot();
            if (pendingWork(restored) || before !== undefined && restored !== before)
              unrecovered = true;
          }
          catch (_) { unrecovered = true; }
          return reject(raw, error, unrecovered || pending);
        } finally { inFlight = false; }
      }};
  }
  globalThis.ReviewSheetEditorTransport = {create};
})();
