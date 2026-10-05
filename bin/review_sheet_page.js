(function () {
  'use strict';
  const data = window.REVIEW_SHEET_DATA;
  const desc = data.description;
  const registry = new Map();
  const registryErrors = [];
  const allItems = [...(desc.items || []),
    ...(desc.groups || []).flatMap(group => group.items)];
  const groups = desc.groups || [];
  const scopeKeys = Object.keys(data.scopes);
  const storageKey = 'review-sheet:' + desc.review;
  const answers = {};
  const mediaState = new Map();
  const verifiedMedia = new Map();
  const mediaViews = new Map();
  const mediaHandles = new Map();
  const failedMedia = new Set();
  const frameCache = new Map();
  let cacheEvictions = 0;
  const mediaPending = [];
  const history = [];
  const reveals = {};
  const comparisonControllers = new Map();
  const liveResources = new Set();
  const pendingNotes = [];
  let focused = 0;
  let storageSafe = true;
  let loadedCount = 0;
  let filter = '';
  let focusMode = false;
  let reviewerValue = '';
  let pageDisposed = false;
  let generation = 0;

  function mediaKey(item, index) { return item.id + ':' + index; }
  function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => {
      resolve = yes; reject = no;
    });
    promise.catch(() => {});
    return {promise, resolve, reject};
  }
  function publishHandles() {
    for (const item of allItems) (item.media || []).forEach((_, index) =>
      mediaHandles.set(mediaKey(item, index), deferred()));
  }
  function clearFrameCache() {
    for (const entry of frameCache.values()) URL.revokeObjectURL(entry.url);
    frameCache.clear();
  }
  async function frameHandle(media, index, signal) {
    const frame = media.frames[index];
    if (!frame || frame.index !== index) throw Error('missing frame');
    if (signal?.aborted) throw Error('frame request cancelled');
    const key = media.src + '#' + frame.src;
    let cached = frameCache.get(key);
    if (cached) {
      frameCache.delete(key); frameCache.set(key, cached);
      return {...cached, source_frame: frame.source_frame,
        phase: frame.phase, index};
    }
    const dataFrame = media.frames.find(row => row.src === frame.src &&
      row.data);
    if (!dataFrame) throw Error('missing embedded frame bytes');
    const raw = bytes(dataFrame.data);
    if (await sha256(raw) !== frame.sha256)
      throw Error('embedded frame hash mismatch');
    if (signal?.aborted) throw Error('frame request cancelled');
    const url = URL.createObjectURL(new Blob([raw], {type: 'image/png'}));
    const image = new Image();
    const cancel = () => image.removeAttribute('src');
    signal?.addEventListener('abort', cancel, {once: true});
    try {
      image.src = url;
      await image.decode();
      if (signal?.aborted) throw Error('frame request cancelled');
    } catch (error) {
      URL.revokeObjectURL(url);
      throw error;
    } finally {
      signal?.removeEventListener('abort', cancel);
    }
    // Concurrent preload and foreground requests may decode the same tile.
    // Keep one cache owner and release the redundant decoded image and URL.
    if (frameCache.has(key)) {
      URL.revokeObjectURL(url);
      image.removeAttribute('src');
      cached = frameCache.get(key);
      return {...cached, source_frame: frame.source_frame,
        phase: frame.phase, index};
    }
    cached = {url, hash: frame.sha256, image};
    frameCache.set(key, cached);
    while (frameCache.size > 32) {
      const oldest = frameCache.keys().next().value;
      URL.revokeObjectURL(frameCache.get(oldest).url);
      frameCache.delete(oldest);
      cacheEvictions++;
    }
    return {...cached, source_frame: frame.source_frame,
      phase: frame.phase, index};
  }

  function el(tag, className, label) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (label !== undefined) node.textContent = String(label);
    return node;
  }
  function button(label, action) {
    const node = el('button', '', label);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }
  function note(text, error) {
    const host = document.querySelector('#messages');
    if (!host) {
      pendingNotes.push([text, error]);
      return null;
    }
    const banner = el('p', 'banner' + (error ? ' error' : ''), text);
    host.append(banner);
    return banner;
  }
  function bytes(base64) {
    const raw = atob(base64);
    const result = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) result[i] = raw.charCodeAt(i);
    return result;
  }
  async function sha256(raw) {
    const hash = await crypto.subtle.digest('SHA-256', raw);
    return [...new Uint8Array(hash)].map(n => n.toString(16).padStart(2, '0'))
      .join('');
  }
  function mediaFor(key) {
    const scope = data.scopes[key];
    return Object.keys(scope.media || {}).every(src => {
      return verifiedMedia.get(src) === scope.media[src];
    });
  }
  function matchingHashes(actual, expected) {
    return actual && typeof actual === 'object' &&
      !Array.isArray(actual) &&
      Object.keys(actual).length === Object.keys(expected).length &&
      Object.entries(expected).every(([src, hash]) => actual[src] === hash);
  }
  function itemReady(item) {
    if (!item) return false;
    return (item.media || []).every((media, index) => {
      const state = mediaState.get(mediaKey(item, index));
      return state && state.ready;
    });
  }
  function blocked(key) {
    if (registryErrors.length) return true;
    const [kind, id] = key.split(':');
    if (kind === 'item') return !itemReady(allItems.find(i => i.id === id));
    if (kind === 'group') {
      const group = groups.find(g => g.id === id);
      return group.items.some(item => !itemReady(item));
    }
    return allItems.some(item => !itemReady(item));
  }
  function state(key) {
    if (blocked(key)) return 'invalid';
    const answer = answers[key];
    if (!answer) return 'unanswered';
    if (answer.state === 'answered' && answer.digest !==
        data.scopes[key].digest) return 'stale';
    if (answer.state === 'answered' &&
        (!validValue(data.scopes[key].definition, answer.value) ||
         (Object.keys(data.scopes[key].media).length &&
          (!matchingHashes(answer.media_hashes, data.scopes[key].media) ||
           !mediaFor(key))))) return 'invalid';
    return answer.state;
  }
  function currentValue(key) {
    return state(key) === 'answered' ? answers[key].value : undefined;
  }
  function save() {
    if (!storageSafe) return;
    try {
      const serialized = JSON.stringify({schema_version: 1,
        review: desc.review, reviewer: reviewerValue, answers, reveals});
      localStorage.setItem(storageKey, serialized);
    } catch (_) {
      note('Draft could not be saved. Export your results now.', true);
      storageSafe = false;
    }
  }
  function discardDraft() {
    if (!confirm('Discard stale and unmatched draft entries?')) return;
    for (const [key, answer] of Object.entries(answers)) {
      if (!data.scopes[key] || answer.digest !== data.scopes[key].digest)
        delete answers[key];
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify({schema_version: 1,
        review: desc.review, reviewer: reviewerValue, answers, reveals}));
      storageSafe = true;
      loadedCount = Object.keys(answers).length;
      note('Stale and unmatched draft entries discarded.');
      renderStatus();
    } catch (_) {
      note('Draft storage is unavailable; export your results.', true);
    }
  }
  function load() {
    let raw;
    try { raw = localStorage.getItem(storageKey); }
    catch (_) {
      storageSafe = false;
      note('Draft storage is unavailable. Export your results.', true);
      return;
    }
    if (!raw) return;
    try {
      const packet = JSON.parse(raw);
      if (packet.schema_version !== 1 || packet.review !== desc.review ||
          !packet.answers ||
          typeof packet.answers !== 'object' ||
          Array.isArray(packet.answers)) throw Error('corrupt draft');
      for (const [key, value] of Object.entries(packet.answers)) {
        if (data.scopes[key] && value && typeof value === 'object' &&
            ['answered', 'inherited', 'stale', 'unanswered'].includes(
              value.state)) answers[key] = value;
      }
      if (typeof packet.reviewer === 'string') reviewerValue = packet.reviewer;
      if (packet.reveals && typeof packet.reveals === 'object') {
        for (const group of groups) if (packet.reveals[group.id]) {
          if (group.layout !== 'synchronized-comparison') continue;
          const pickKey = 'group:' + group.id + ':pick';
          if (packet.reveals[group.id].digest !== data.scopes[pickKey].digest) {
            storageSafe = false;
            if (answers[pickKey]) answers[pickKey] = {
              ...answers[pickKey], state: 'stale'};
            note('Stored comparison reveal history is stale and was '
              + 'preserved. Reconfirm the best decision for this case.', true);
            continue;
          }
          if (!validRevealState(packet.reveals[group.id]))
            throw Error('corrupt reveal history');
          reveals[group.id] = packet.reveals[group.id];
        }
      }
      loadedCount = Object.keys(packet.answers).length;
      if (loadedCount !== Object.keys(answers).length) {
        storageSafe = false;
        note('Some stored answers are unreadable. The draft was preserved; '
          + 'export and inspect it before continuing.', true);
      }
    } catch (_) {
      storageSafe = false;
      note('Stored draft is unreadable and was preserved. Export will omit '
        + 'the unreadable data.', true);
    }
  }
  function prefill() {
    for (const item of allItems) {
      const source = item.prefill;
      if (!source || !source.values || !source.source) continue;
      for (const [name, value] of Object.entries(source.values)) {
        const key = 'item:' + item.id + ':' + name;
        if (data.scopes[key] && !answers[key]) answers[key] = {
          state: 'inherited', value, digest: data.scopes[key].digest,
          source: source.source};
      }
    }
  }
  function setAnswer(key, value) {
    if (blocked(key)) {
      note('Media could not load; this decision is unavailable.', true);
      return;
    }
    const definition = data.scopes[key].definition;
    if (definition.kind === 'text' &&
        new TextEncoder().encode(value).length > data.limits.text_answer) {
      note('Text answer exceeds the size limit.', true);
      return;
    }
    const answer = {state: 'answered', value,
      digest: data.scopes[key].digest, time: new Date().toISOString()};
    if (Object.keys(data.scopes[key].media).length) {
      if (!mediaFor(key)) {
        note('Embedded media hash has not been verified.', true);
        return;
      }
      answer.media_hashes = {...data.scopes[key].media};
      answer.evidence = 'verified';
    } else answer.evidence = 'unverified';
    history.push([key, answers[key] ? structuredClone(answers[key]) : null]);
    if (history.length > 100) history.shift();
    answers[key] = answer;
    const groupId = comparisonPickGroup(key);
    if (groupId) advanceDecisionRevision(groupId);
    save();
    renderStatus();
  }
  function undo() {
    const step = history.pop();
    if (!step) return;
    if (step[1] === null) delete answers[step[0]];
    else answers[step[0]] = step[1];
    const groupId = comparisonPickGroup(step[0]);
    if (groupId) advanceDecisionRevision(groupId);
    save();
    renderStatus();
  }
  function comparisonPickGroup(key) {
    if (!key.startsWith('group:') || !key.endsWith(':pick')) return null;
    const id = key.slice(6, -5);
    return groups.some(group => group.id === id &&
      group.layout === 'synchronized-comparison') ? id : null;
  }
  function revealState(groupId) {
    return reveals[groupId] ||= {
      digest: data.scopes['group:' + groupId + ':pick'].digest,
      revealed: false, first_reveal: null,
      decision_revision: 0, event_sequence: 0};
  }
  function validRevealState(value) {
    if (!value || !Number.isInteger(value.decision_revision) ||
        value.decision_revision < 0 ||
        !Number.isInteger(value.event_sequence) ||
        typeof value.revealed !== 'boolean') return false;
    if (value.first_reveal === null)
      return !value.revealed &&
        value.event_sequence === value.decision_revision;
    const first = value.first_reveal;
    return value.revealed && first &&
      Number.isInteger(first.sequence) && first.sequence > 0 &&
      Number.isInteger(first.next_decision_revision) &&
      first.sequence === first.next_decision_revision &&
      first.sequence <= value.event_sequence &&
      first.next_decision_revision <= value.decision_revision + 1 &&
      value.event_sequence === value.decision_revision + 1;
  }
  function advanceDecisionRevision(groupId) {
    const value = revealState(groupId);
    value.decision_revision++;
    value.event_sequence++;
  }
  function reveal(groupId) {
    const value = revealState(groupId);
    if (!value.first_reveal) {
      value.event_sequence++;
      value.first_reveal = {sequence: value.event_sequence,
        next_decision_revision: value.decision_revision + 1};
    }
    value.revealed = true;
    save();
  }
  function exportReveal(groupId) {
    const value = revealState(groupId);
    const answered = state('group:' + groupId + ':pick') === 'answered';
    return {digest: data.scopes['group:' + groupId + ':pick'].digest,
      revealed: value.revealed,
      first_reveal: value.first_reveal,
      decision_revision: value.decision_revision,
      event_sequence: value.event_sequence,
      revealed_before_decision: answered ? Boolean(value.first_reveal &&
        value.decision_revision >=
        value.first_reveal.next_decision_revision) : null};
  }
  function allScopes(item) {
    return scopeKeys.filter(key => key.startsWith('item:' + item.id + ':'));
  }
  function activeScopes(item) {
    const group = groups.find(entry => entry.items.includes(item));
    return [...allScopes(item), ...scopeKeys.filter(key =>
      group && key.startsWith('group:' + group.id + ':')),
    ...scopeKeys.filter(key => key.startsWith('review:'))];
  }
  function progress(keys) {
    const required = keys.filter(key => data.scopes[key].definition.required);
    const done = required.filter(key => state(key) === 'answered');
    return [done.length, required.length];
  }
  function renderStatus() {
    for (const node of document.querySelectorAll('[data-scope-state]')) {
      node.textContent = state(node.dataset.scopeState);
    }
    const [done, total] = progress(scopeKeys);
    document.querySelector('#progress').value = done;
    document.querySelector('#progress').max = Math.max(1, total);
    document.querySelector('#progress-text').textContent =
      done + ' of ' + total + ' required decisions answered';
    for (const group of groups) {
      const keys = scopeKeys.filter(key =>
        key.startsWith('group:' + group.id + ':') ||
        group.items.some(item => key.startsWith('item:' + item.id + ':')));
      const node = document.getElementById('progress-' + group.id);
      if (node) node.textContent = progress(keys).join(' of ') + ' required';
    }
    for (const node of document.querySelectorAll('[data-choice]')) {
      node.setAttribute('aria-pressed', String(
        currentValue(node.dataset.choice) === node.dataset.value));
    }
    for (const node of document.querySelectorAll('[data-flag]')) {
      node.setAttribute('aria-pressed', String(
        (currentValue(node.dataset.flag) || []).includes(node.dataset.value)));
    }
  }
  function decision(key) {
    const definition = data.scopes[key].definition;
    const wrapper = el('div', 'decision' +
      (definition.authority ? ' authority' : ''));
    const title = el('div', '', definition.question || key.split(':').at(-1));
    wrapper.append(title);
    const status = el('span', 'state');
    status.dataset.scopeState = key;
    wrapper.append(status);
    if (definition.authority) wrapper.append(el('p', '',
      'Owner authority: choose a value, then confirm explicitly.'));
    const options = el('div', 'options');
    const choose = value => {
      if (definition.authority) {
        let pending = wrapper.querySelector('[data-confirm]');
        if (pending) pending.remove();
        pending = button('Confirm ' + JSON.stringify(value), () => {
          setAnswer(key, value); pending.remove();
        });
        pending.dataset.confirm = 'true';
        wrapper.append(pending);
      } else setAnswer(key, value);
    };
    if (definition.kind === 'choice' || definition.kind === 'boolean') {
      const values = definition.kind === 'boolean' ? [true, false] :
        definition.options;
      values.forEach((value, index) => {
        const hotkey = definition.keys && definition.keys[index];
        const groupId = comparisonPickGroup(key);
        const label = groupId && value !== 'tie' && value !== 'none' ?
          String.fromCharCode(65 + groups.find(group => group.id === groupId)
            .comparison.candidates.indexOf(value)) : String(value);
        const control = button((hotkey ? hotkey + ' · ' : '') + label,
          () => choose(value));
        control.dataset.choice = key;
        control.dataset.value = String(value);
        options.append(control);
      });
    } else if (definition.kind === 'flags') {
      definition.options.forEach((value, index) => {
        const hotkey = definition.keys && definition.keys[index] ||
          String(index + 1);
        const control = button(hotkey + ' · ' + value, () => {
          const chosen = [...(currentValue(key) || [])];
          const at = chosen.indexOf(value);
          if (at < 0) chosen.push(value); else chosen.splice(at, 1);
          choose(chosen);
        });
        control.dataset.flag = key;
        control.dataset.value = value;
        options.append(control);
      });
    } else {
      const input = definition.kind === 'text' ? el('textarea') : el('input');
      if (definition.kind === 'number') {
        input.type = 'number';
        if (definition.min !== undefined) input.min = definition.min;
        if (definition.max !== undefined) input.max = definition.max;
      }
      input.setAttribute('aria-label', definition.question || key);
      const previous = answers[key];
      if (previous && previous.value !== undefined)
        input.value = previous.value;
      options.append(input);
      if (definition.unit) options.append(el('span', '', definition.unit));
      options.append(button('Save answer', () => {
        if (definition.kind === 'number') {
          if (!input.value || !input.checkValidity()) {
            note('Number is missing or outside its bounds.', true); return;
          }
          choose(Number(input.value));
        } else choose(input.value);
      }));
    }
    wrapper.append(options);
    const answer = answers[key];
    if (answer && answer.state === 'inherited') wrapper.append(el('p', '',
      'Prior answer from ' + answer.source + ': ' +
      JSON.stringify(answer.value) + '. Reconfirm to answer.'));
    return wrapper;
  }
  async function renderMedia(media, parent, item, index) {
    const shell = el('div', 'media');
    parent.append(shell);
    const component = registry.get(media.kind);
    const handle = mediaHandles.get(mediaKey(item, index));
    const stateKey = mediaKey(item, index);
    if (!component) {
      mediaState.set(stateKey, {src: media.src, ready: false});
      shell.append(el('p', 'error', 'Unknown or invalid media component'));
      handle.reject(Error('missing component'));
      return;
    }
    const spec = data.components[media.kind];
    const resources = {urls: [], workers: [], disposed: false};
    liveResources.add(resources);
    const group = groups.find(g => g.items.includes(item));
    let readyCalled = false;
    let failed = false;
    let view = null;
    let display = null;
    const settleReady = () => {
      if (failed || resources.disposed || pageDisposed) return;
      const previous = mediaState.get(stateKey);
      mediaState.set(stateKey, {...previous, ready: true});
      handle.resolve(Object.freeze({url: api.url,
        bytes: api.bytes?.slice() || null,
        play: () => {
          if (resources.disposed || pageDisposed) throw Error('media disposed');
          return display?.play?.();
        }, pause: () => display?.pause?.()}));
      renderStatus();
    };
    const failMedia = message => {
      if (failed) return;
      failed = true;
      resources.disposed = true;
      failedMedia.add(stateKey);
      mediaState.set(stateKey, {src: media.src, ready: false});
      shell.append(el('p', 'error', message));
      handle.reject(Error(message));
      if (view) try { component.dispose?.(view, api); } catch (_) {}
      for (const worker of resources.workers) worker.terminate();
      for (const url of resources.urls) URL.revokeObjectURL(url);
      liveResources.delete(resources);
      renderStatus();
    };
    const api = {
      url: null, bytes: null,
      settings: Object.freeze({gain: desc.settings?.gain ?? 0.15,
        gainCap: 0.5, privacy: desc.privacy,
        darkMode: matchMedia('(prefers-color-scheme: dark)').matches,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches}),
      group: group ? Object.freeze({id: group.id, layout: group.layout,
        members: Object.freeze(group.items.map(entry => Object.freeze({
          id: entry.id, label: entry.title || entry.id}))),
        get focused() {
          return group.items.includes(allItems[focused]) ?
            allItems[focused].id : null;
        }}) : null,
      media: (itemId, mediaIndex) => {
        if (resources.disposed || pageDisposed)
          return Promise.reject(Error('media disposed'));
        if (!group || !group.items.some(entry => entry.id === itemId))
          return Promise.reject(Error('missing sibling media'));
        const siblingKey = itemId + ':' + mediaIndex;
        if (failedMedia.has(siblingKey))
          return Promise.reject(Error('failed sibling media'));
        const sibling = mediaHandles.get(siblingKey);
        return sibling ? sibling.promise.then(value => {
          if (resources.disposed || pageDisposed) throw Error('media disposed');
          if (failedMedia.has(siblingKey)) throw Error('failed sibling media');
          return value;
        }) : Promise.reject(Error('missing sibling media'));
      },
      frame: (frameIndex, signal) => {
        if (resources.disposed || pageDisposed)
          return Promise.reject(Error('media disposed'));
        return frameHandle(media, frameIndex, signal);
      },
      ready: () => {
        readyCalled = true;
        if (view && (!display || media.kind === 'frame-sequence'))
          settleReady();
      },
      fail: message => failMedia(String(message)),
      note: message => note(message),
      worker: name => {
        if (resources.disposed || pageDisposed)
          throw Error('media disposed');
        if (!Object.hasOwn(spec.workers, name))
          throw Error('undeclared worker: ' + name);
        const source = new TextDecoder().decode(bytes(spec.workers[name]));
        const url = URL.createObjectURL(new Blob([source],
          {type: 'text/javascript'}));
        resources.urls.push(url);
        const worker = new Worker(url);
        resources.workers.push(worker);
        return worker;
      },
      wasm: (name, imports = {}) => {
        if (resources.disposed || pageDisposed)
          return Promise.reject(Error('media disposed'));
        if (!Object.hasOwn(spec.wasm, name))
          return Promise.reject(Error('undeclared wasm: ' + name));
        return WebAssembly.instantiate(bytes(spec.wasm[name]), imports)
          .then(value => {
            if (resources.disposed || pageDisposed)
              throw Error('media disposed');
            return value;
          });
      }
    };
    try {
      if (media.kind === 'frame-sequence') {
        const seen = new Set();
        for (const frame of media.frames) {
          if (seen.has(frame.src)) continue;
          seen.add(frame.src);
          const source = media.frames.find(row => row.src === frame.src &&
            row.data);
          if (!source || await sha256(bytes(source.data)) !== frame.sha256)
            throw Error('embedded frame hash mismatch');
          verifiedMedia.set(media.src + '#' + frame.src, frame.sha256);
        }
        mediaState.set(stateKey, {src: media.src, ready: false,
          hash: media.sha256});
      } else if (media.mode === 'embed') {
        const raw = bytes(media.data);
        const hash = await sha256(raw);
        if (hash !== media.sha256) throw Error('embedded hash mismatch');
        verifiedMedia.set(media.src, hash);
        api.bytes = raw.slice();
        api.url = URL.createObjectURL(new Blob([raw], {type: media.mime}));
        resources.urls.push(api.url);
        mediaState.set(stateKey, {src: media.src, ready: false, hash,
          url: api.url});
      } else if (media.kind !== 'link') {
        api.url = media.src;
        mediaState.set(stateKey, {src: media.src, ready: false,
          hash: null, url: media.src});
      } else mediaState.set(stateKey, {src: media.src, ready: false});
      Object.freeze(api);
      view = await component.render(media, api);
      if (!(view instanceof Element) || failed || resources.disposed ||
          pageDisposed)
        throw Error('component render did not return a live element');
      display = view.matches('img,audio,video') ? view :
        view.querySelector('img,audio,video');
      if (display && media.kind !== 'frame-sequence') {
        const eventName = display.tagName === 'IMG' ? 'load' :
          'loadedmetadata';
        if (display.tagName === 'IMG' && display.complete &&
            display.naturalWidth || display.readyState >= 1) {
          settleReady();
        } else display.addEventListener(eventName, settleReady,
          {once: true});
      }
      display?.addEventListener('error', () => {
        failMedia('Media could not load. Decisions disabled.');
      });
      shell.append(view);
      if (!mediaViews.has(item.id)) mediaViews.set(item.id, []);
      mediaViews.get(item.id).push({component, view, api, resources,
        media, index, fail: failMedia});
      if (allItems[focused] === item) component.focus?.(view, api);
      else component.blur?.(view, api);
      if ((readyCalled && (!display || media.kind === 'frame-sequence')) ||
          mediaState.get(stateKey)?.ready) settleReady();
      renderStatus();
    } catch (error) {
      if (failed && view) try { component.dispose?.(view, api); }
      catch (_) {}
      failMedia(media.mode === 'embed' ?
        'Embedded media verification failed.' :
        'Media could not load. Decisions disabled.');
    }
  }
  function renderItem(item) {
    const card = el('article', 'item');
    card.dataset.itemId = item.id;
    card.tabIndex = -1;
    card.append(el('h3', '', item.title || item.id));
    if (item.fields) {
      const fields = el('dl', 'fields');
      for (const [name, value] of Object.entries(item.fields)) {
        fields.append(el('dt', '', name), el('dd', '', String(value)));
      }
      card.append(fields);
    }
    (item.media || []).forEach((media, index) =>
      mediaPending.push(renderMedia(media, card, item, index)));
    for (const key of allScopes(item)) card.append(decision(key));
    card.addEventListener('click', () => focusItem(allItems.indexOf(item)));
    return card;
  }
  function renderComparison(group, section) {
    const info = group.comparison;
    const host = el('div', 'comparison');
    host.dataset.caseId = group.id;
    const controls = el('div', 'comparison-controls');
    const referencePanel = el('div', 'comparison-panel');
    const candidatePanel = el('div', 'comparison-panel');
    referencePanel.append(el('h3', '', 'Reference'));
    candidatePanel.append(el('h3', '', 'Candidate A'));
    const panels = el('div', 'comparison-panels');
    panels.append(referencePanel, candidatePanel);
    const status = el('p', 'comparison-status', 'Verifying frames…');
    host.append(controls, panels, status);
    const assets = el('div', 'comparison-assets');
    group.items.forEach(item => assets.append(renderItem(item)));
    section.append(host, assets);
    const controller = {group, info, host, controls, status,
      panels: [referencePanel, candidatePanel], assets,
      candidate: 0, view: info.views[0], crop: Object.keys(info.crops)[0],
      index: 0, speed: 1, loopStart: 0, loopEnd: info.frame_count,
      playing: false, timer: null, active: false, failed: false,
      visibleIdentity: false};
    comparisonControllers.set(group.id, controller);
    function controlsButton(label, fn) { controls.append(button(label, fn)); }
    const play = button('Play', () => {
      controller.playing = !controller.playing;
      play.textContent = controller.playing ? 'Pause' : 'Play';
      if (controller.playing) {
        controller.clockOrigin = performance.now();
        controller.clockIndex = controller.index;
        tick();
      }
      else clearTimeout(controller.timer);
    });
    controls.append(play);
    controlsButton('Previous frame', () => {
      controller.playing = false; play.textContent = 'Play';
      clearTimeout(controller.timer);
      show(Math.max(0, controller.index - 1));
    });
    controlsButton('Next frame', () => {
      controller.playing = false; play.textContent = 'Play';
      clearTimeout(controller.timer);
      show(Math.min(info.frame_count - 1, controller.index + 1));
    });
    const scrub = el('input'); scrub.type = 'range'; scrub.min = 0;
    scrub.max = info.frame_count - 1; scrub.value = '0';
    scrub.setAttribute('aria-label', 'Exact display frame');
    scrub.addEventListener('input', () => {
      controller.playing = false; play.textContent = 'Play';
      clearTimeout(controller.timer);
      show(Number(scrub.value));
    });
    controls.append(scrub);
    const speed = el('select');
    speed.setAttribute('aria-label', 'Playback speed');
    for (const value of [0.125, 0.25, 0.5, 1]) {
      const option = el('option', '', value + '×');
      option.value = value;
      if (value === 1) option.selected = true;
      speed.append(option);
    }
    speed.addEventListener('change', () => {
      controller.speed = Number(speed.value);
      controller.clockOrigin = performance.now();
      controller.clockIndex = controller.index;
    });
    controls.append(speed);
    const start = el('input'); start.type = 'number'; start.min = 0;
    start.max = info.frame_count - 1; start.value = 0;
    start.setAttribute('aria-label', 'Loop start frame');
    const end = el('input'); end.type = 'number'; end.min = 1;
    end.max = info.frame_count; end.value = info.frame_count;
    end.setAttribute('aria-label', 'Loop end frame exclusive');
    for (const input of [start, end]) input.addEventListener('change', () => {
      const a = Number(start.value), b = Number(end.value);
      if (Number.isInteger(a) && Number.isInteger(b) && a >= 0 &&
          b <= info.frame_count && a < b) {
        controller.loopStart = a; controller.loopEnd = b;
        controller.clockOrigin = performance.now();
        controller.clockIndex = controller.index;
      } else note('Loop range must be [start, end).', true);
    });
    controls.append(start, end);
    const candidate = el('select');
    candidate.setAttribute('aria-label', 'Candidate');
    info.candidates.forEach((id, index) => {
      const option = el('option', '', String.fromCharCode(65 + index));
      option.value = id; candidate.append(option);
    });
    candidate.addEventListener('change', () => {
      controller.candidate = info.candidates.indexOf(candidate.value);
      candidatePanel.querySelector('h3').textContent = 'Candidate ' +
        String.fromCharCode(65 + controller.candidate);
      show(controller.index);
    });
    controls.append(candidate);
    const angle = el('select'); angle.setAttribute('aria-label', 'View');
    for (const value of info.views) {
      const option = el('option', '', value); option.value = value;
      angle.append(option);
    }
    angle.addEventListener('change', () => {
      controller.view = angle.value; show(controller.index);
    });
    controls.append(angle);
    const crop = el('select'); crop.setAttribute('aria-label', 'Crop');
    for (const value of Object.keys(info.crops)) {
      const option = el('option', '', value); option.value = value;
      crop.append(option);
    }
    crop.addEventListener('change', () => {
      controller.crop = crop.value; show(controller.index);
    });
    controls.append(crop);
    controlsButton('Next supplied key', () => {
      const next = info.key_frames.find(value =>
        value + info.hold_start > controller.index);
      show((next ?? info.key_frames[0] ?? 0) + info.hold_start);
    });
    controlsButton('Expand', () => host.requestFullscreen?.());
    controlsButton('Reveal identities', () => {
      reveal(group.id); controller.visibleIdentity = true;
      candidatePanel.querySelector('h3').textContent =
        'Candidate ' + String.fromCharCode(65 + controller.candidate) +
        ': ' + (info.reveal?.[info.candidates[controller.candidate]] ||
          info.candidates[controller.candidate]);
    });
    function sequence(itemId) {
      return (mediaViews.get(itemId) || []).find(entry =>
        entry.media.kind === 'frame-sequence' &&
        entry.media.view === controller.view);
    }
    function applyCrop() {
      const rect = info.crops[controller.crop];
      for (const panel of controller.panels) {
        const box = panel.querySelector('.frame-sequence');
        if (!box) continue;
        const image = box.querySelector('img');
        const entry = [...mediaViews.values()].flat().find(row =>
          row.view === box);
        box.style.aspectRatio = rect[2] + '/' + rect[3];
        image.style.width = entry.media.width / rect[2] * 100 + '%';
        image.style.height = 'auto';
        image.style.left = -rect[0] / rect[2] * 100 + '%';
        image.style.top = -rect[1] / rect[3] * 100 + '%';
      }
    }
    async function show(index) {
      if (!controller.active || controller.failed) return;
      controller.abort?.abort();
      controller.abort = new AbortController();
      const signal = controller.abort.signal;
      const token = ++generation;
      host.classList.add('comparison-pending');
      host.dataset.pendingIndex = String(index);
      status.textContent = 'Loading display ' + index + '…';
      const reference = sequence(info.reference);
      const selected = sequence(info.candidates[controller.candidate]);
      if (!reference || !selected) return;
      try {
        const [left, right] = await Promise.all([
          reference.component.showFrame(index, reference.view,
            reference.api, signal),
          selected.component.showFrame(index, selected.view, selected.api,
            signal)]);
        if (token !== generation || !controller.active) return;
        if (left.frame.index !== index || right.frame.index !== index ||
            left.frame.source_frame !== right.frame.source_frame ||
            left.frame.phase !== right.frame.phase)
          throw Error('paired source frame mismatch');
        for (const [entry, panel] of [[reference, referencePanel],
                                      [selected, candidatePanel]]) {
          const old = panel.querySelector('.frame-sequence');
          if (old && old !== entry.view) {
            old.querySelector('img')?.removeAttribute('src');
            assets.append(old);
          }
          panel.append(entry.view);
        }
        left.commit(); right.commit(); applyCrop();
        host.classList.remove('comparison-pending');
        controller.index = index; scrub.value = index;
        const phase = left.frame.phase.replace('_', ' ');
        status.textContent = 'Display ' + index + ' · source ' +
          left.frame.source_frame + ' · ' + phase +
          (info.key_frames.includes(left.frame.source_frame) ?
            ' · supplied key' : '') +
          (index === controller.loopEnd - 1 ? ' · loop restart cut' : '');
        for (const offset of [1, 2]) {
          const next = index + offset;
          if (next >= info.frame_count) break;
          Promise.allSettled([
            reference.component.showFrame(next, reference.view,
              reference.api, signal),
            selected.component.showFrame(next, selected.view,
              selected.api, signal)]);
        }
      } catch (error) {
        if (token !== generation || !controller.active) return;
        controller.failed = true;
        controller.playing = false; play.textContent = 'Play';
        clearTimeout(controller.timer);
        status.textContent = 'Frame failed: ' + error;
        for (const item of group.items) (item.media || []).forEach(
          (media, mediaIndex) => mediaState.set(mediaKey(item, mediaIndex),
            {src: media.src, ready: false}));
        renderStatus();
      }
    }
    async function tick() {
      if (!controller.playing || !controller.active || controller.failed)
        return;
      const rate = info.fps[0] / info.fps[1] * controller.speed;
      const elapsed = Math.floor((performance.now() -
        controller.clockOrigin) * rate / 1000);
      const length = controller.loopEnd - controller.loopStart;
      const initial = controller.clockIndex >= controller.loopStart &&
        controller.clockIndex < controller.loopEnd ?
        controller.clockIndex : controller.loopStart;
      const next = controller.loopStart +
        ((initial - controller.loopStart + elapsed + length) % length);
      if (next !== controller.index) await show(next);
      if (controller.playing)
        controller.timer = setTimeout(tick, Math.max(4,
          Math.min(32, 500 / rate)));
    }
    controller.activate = () => {
      controller.active = true;
      controller.visibleIdentity = false;
      candidatePanel.querySelector('h3').textContent = 'Candidate ' +
        String.fromCharCode(65 + controller.candidate);
      show(controller.index);
    };
    controller.deactivate = () => {
      controller.active = false; controller.playing = false;
      controller.abort?.abort();
      play.textContent = 'Play'; clearTimeout(controller.timer);
      generation++; clearFrameCache();
      for (const item of group.items)
        for (const entry of mediaViews.get(item.id) || [])
          entry.view.querySelectorAll('img').forEach(image =>
            image.removeAttribute('src'));
    };
    controller.show = show;
    Promise.allSettled(mediaPending).then(() => {
      if (group.items.includes(allItems[focused])) controller.activate();
    });
  }
  function focusItem(index) {
    if (!allItems.length) return;
    const previousGroup = groups.find(group =>
      group.items.includes(allItems[focused]));
    const previous = document.querySelector('.item.focused');
    if (previous) {
      previous.classList.remove('focused');
      for (const node of previous.querySelectorAll('audio,video')) node.pause();
      for (const entry of mediaViews.get(previous.dataset.itemId) || [])
        try { entry.component.blur?.(entry.view, entry.api); }
        catch (error) { entry.fail('Component blur failed: ' + error); }
    }
    focused = (index + allItems.length) % allItems.length;
    const nextGroup = groups.find(group =>
      group.items.includes(allItems[focused]));
    if (previousGroup !== nextGroup) {
      comparisonControllers.get(previousGroup?.id)?.deactivate();
      comparisonControllers.get(nextGroup?.id)?.activate();
    }
    const card = [...document.querySelectorAll('.item')].find(node =>
      node.dataset.itemId === allItems[focused].id);
    if (card) {
      card.classList.add('focused');
      for (const entry of mediaViews.get(card.dataset.itemId) || [])
        try { entry.component.focus?.(entry.view, entry.api); }
        catch (error) { entry.fail('Component focus failed: ' + error); }
      card.scrollIntoView({block: 'nearest'});
      card.focus({preventScroll: true});
    }
  }
  function render() {
    const app = document.getElementById('app');
    const header = el('header');
    header.append(el('h1', '', desc.title || desc.review));
    if (desc.instructions) header.append(el('p', '', desc.instructions));
    for (const caveat of desc.caveats || []) header.append(el('p', '', caveat));
    const toolbar = el('div', 'toolbar');
    const bar = el('progress'); bar.id = 'progress';
    const progressText = el('span'); progressText.id = 'progress-text';
    toolbar.append(bar, progressText,
      button('Export results', exportResults),
      button('Import results', () =>
        document.getElementById('import-file').click()),
      button('Discard stale draft entries', discardDraft),
      button('Keys (?)', showLegend));
    const file = el('input'); file.id = 'import-file'; file.type = 'file';
    file.accept = 'application/json,.json'; file.className = 'hidden';
    file.addEventListener('change', importResults);
    toolbar.append(file);
    header.append(toolbar);
    const messages = el('div'); messages.id = 'messages';
    header.append(messages);
    app.append(header);
    for (const [message, error] of pendingNotes) note(message, error);
    pendingNotes.length = 0;
    const reviewer = el('input'); reviewer.id = 'reviewer';
    reviewer.placeholder = 'Reviewer (optional metadata)';
    reviewer.setAttribute('aria-label', 'Reviewer (optional metadata)');
    reviewer.value = reviewerValue;
    reviewer.addEventListener('input', () => {
      reviewerValue = reviewer.value; save();
    });
    app.append(reviewer);
    for (const key of scopeKeys.filter(key => key.startsWith('review:')))
      app.append(decision(key));
    for (const group of groups) {
      const section = el('section', 'group');
      section.dataset.groupId = group.id;
      section.append(el('h2', '', group.title || group.id));
      const count = el('span'); count.id = 'progress-' + group.id;
      section.append(count);
      for (const key of scopeKeys.filter(key => key.startsWith('group:' +
        group.id + ':'))) section.append(decision(key));
      if (group.layout === 'synchronized-comparison') {
        renderComparison(group, section);
      } else {
        const list = el('div', group.layout === 'grid' ? 'grid' : 'list');
        group.items.forEach(item => list.append(renderItem(item)));
        section.append(list);
      }
      app.append(section);
    }
    (desc.items || []).forEach(item => app.append(renderItem(item)));
    const dialog = el('dialog'); dialog.id = 'dialog'; app.append(dialog);
    renderStatus(); focusItem(0);
    note('Exports download as review-results.json. Move private results to '
      + 'the private review directory before import.');
  }
  function packet() {
    const exported = {};
    let unverified = 0;
    for (const key of scopeKeys) {
      const answer = answers[key];
      if (answer && answer.state === 'answered' &&
          (blocked(key) || (Object.keys(data.scopes[key].media).length &&
            !mediaFor(key)))) {
        exported[key] = {state: 'unanswered',
          digest: data.scopes[key].digest};
        unverified++;
      } else exported[key] = answer ? {...answer, state: state(key)} :
        {state: 'unanswered', digest: data.scopes[key].digest};
    }
    if (unverified) note(unverified + ' answers could not be verified '
      + 'because media failed to load or hash; exported as unanswered.', true);
    const result = {schema_version: 1, review: desc.review,
      resolved_sha256: data.resolved_sha256,
      context: desc.context || {}, reviewer: document.getElementById(
        'reviewer').value, answers: exported,
      reveals: Object.fromEntries(groups.filter(group =>
        group.layout === 'synchronized-comparison').map(group =>
        [group.id, exportReveal(group.id)]))};
    return result;
  }
  async function exportResults() {
    await Promise.allSettled(mediaPending);
    await Promise.race([Promise.allSettled([...mediaHandles.values()].map(
      handle => handle.promise)), new Promise(resolve =>
      setTimeout(resolve, 3000))]);
    const result = packet();
    const incomplete = scopeKeys.filter(key => data.scopes[key].definition
      .required && result.answers[key].state !== 'answered');
    note(incomplete.length ? incomplete.length + ' required answers remain.' :
      'All required answers recorded.');
    const output = JSON.stringify(result, null, 2);
    if (new TextEncoder().encode(output).length > data.limits.results) {
      note('Results exceed the export size limit.', true); return;
    }
    const url = URL.createObjectURL(new Blob([output],
      {type: 'application/json'}));
    const link = el('a'); link.href = url;
    link.download = 'review-results.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function importResults(event) {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > data.limits.results) {
      note('Results file exceeds the size limit.', true); return;
    }
    let packet;
    try { packet = JSON.parse(await file.text()); }
    catch (_) { note('Results file is not valid JSON.', true); return; }
    await Promise.allSettled(mediaPending);
    await Promise.race([Promise.allSettled([...mediaHandles.values()].map(
      handle => handle.promise)), new Promise(resolve =>
      setTimeout(resolve, 3000))]);
    if (!packet || packet.schema_version !== 1 ||
        packet.review !== desc.review || !packet.answers ||
        typeof packet.answers !== 'object' ||
        Array.isArray(packet.answers)) {
      note('Results are for a different review or malformed.', true); return;
    }
    if (packet.resolved_sha256 !== data.resolved_sha256) {
      note('Results use a different frozen description.', true); return;
    }
    for (const group of groups) {
      if (group.layout !== 'synchronized-comparison') continue;
      const incoming = packet.reveals?.[group.id];
      if (!incoming || incoming.digest !==
          data.scopes['group:' + group.id + ':pick'].digest) {
        note('Comparison reveal history is missing or stale.', true);
        return;
      }
      const answered = packet.answers['group:' + group.id + ':pick']?.state
        === 'answered';
      const expectedBefore = answered ? Boolean(incoming.first_reveal &&
        incoming.decision_revision >=
        incoming.first_reveal.next_decision_revision) : null;
      if (!validRevealState(incoming) ||
          incoming.revealed_before_decision !== expectedBefore) {
        note('Comparison reveal history is invalid.', true);
        return;
      }
      const local = revealState(group.id);
      if (local.first_reveal && incoming.first_reveal &&
          JSON.stringify(local.first_reveal) !==
          JSON.stringify(incoming.first_reveal)) {
        note('Conflicting first-reveal histories need a person to resolve.',
          true);
        return;
      }
      if (!local.first_reveal && incoming.first_reveal) {
        local.first_reveal = incoming.first_reveal;
        local.revealed = true;
      }
      local.decision_revision = Math.max(local.decision_revision,
        incoming.decision_revision || 0);
      local.event_sequence = Math.max(local.event_sequence,
        incoming.event_sequence || 0);
    }
    if (typeof packet.reviewer === 'string' &&
        !document.getElementById('reviewer').value) {
      document.getElementById('reviewer').value = packet.reviewer;
      reviewerValue = packet.reviewer;
    }
    const conflicts = [];
    let rejected = 0;
    for (const [key, incoming] of Object.entries(packet.answers)) {
      if (!data.scopes[key] || !incoming || incoming.state !== 'answered')
        continue;
      const scope = data.scopes[key];
      if (incoming.digest !== scope.digest ||
          !validValue(scope.definition, incoming.value) ||
          (Object.keys(scope.media).length &&
           (!matchingHashes(incoming.media_hashes, scope.media) ||
            !mediaFor(key)))) {
        rejected++;
        continue;
      }
      const imported = {...incoming,
        state: scope.authority ? 'inherited' : 'answered',
        evidence: Object.keys(scope.media).length ?
          'verified' : 'unverified'};
      if (scope.authority) imported.source = 'imported file';
      const existing = answers[key];
      if (existing && existing.state === 'answered' &&
          JSON.stringify(existing.value) !== JSON.stringify(incoming.value)) {
        conflicts.push([key, existing, imported]);
      } else if (!existing || existing.state !== 'answered') {
        answers[key] = imported;
        const groupId = comparisonPickGroup(key);
        if (groupId) advanceDecisionRevision(groupId);
      }
    }
    if (rejected) note(rejected + ' invalid or stale imported answers '
      + 'were rejected.', true);
    if (conflicts.length) {
      const dialog = document.getElementById('dialog');
      dialog.replaceChildren(el('h2', '', 'Choose each conflicting answer'));
      for (const [key, local, incoming] of conflicts) {
        const row = el('div', 'conflict');
        row.append(el('p', '', key),
          button('Keep draft: ' + JSON.stringify(local.value), () =>
            row.remove()),
          button('Use imported: ' + JSON.stringify(incoming.value), () => {
            answers[key] = incoming; row.remove();
            const groupId = comparisonPickGroup(key);
            if (groupId) advanceDecisionRevision(groupId);
            save(); renderStatus();
          }));
        dialog.append(row);
      }
      dialog.append(button('Close', () => dialog.close()));
      dialog.showModal();
    }
    save(); renderStatus();
    event.target.value = '';
  }
  function validValue(definition, value) {
    if (definition.kind === 'choice')
      return typeof value === 'string' && definition.options.includes(value);
    if (definition.kind === 'boolean') return typeof value === 'boolean';
    if (definition.kind === 'flags')
      return Array.isArray(value) && new Set(value).size === value.length &&
        value.every(v => typeof v === 'string' &&
          definition.options.includes(v));
    if (definition.kind === 'text') return typeof value === 'string' &&
      new TextEncoder().encode(value).length <= data.limits.text_answer;
    return typeof value === 'number' && Number.isFinite(value) &&
      (definition.min === undefined || value >= definition.min) &&
      (definition.max === undefined || value <= definition.max);
  }
  function showLegend() {
    const dialog = document.getElementById('dialog');
    dialog.replaceChildren(el('h2', '', 'Keyboard controls'),
      el('p', '', 'j/k item · J/K group · n next unanswered · f focus mode '
        + '· / filter · u undo · ? legend. Choice and flag keys appear '
        + 'beside their options. Authority decisions require confirmation.'),
      button('Close', () => dialog.close()));
    const item = allItems[focused];
    for (const kind of new Set((item?.media || []).map(media => media.kind)))
      for (const [key, label] of Object.entries(registry.get(kind)?.keys || {}))
        dialog.append(el('p', '', key + ' · ' + label));
    dialog.showModal();
  }
  function keydown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (document.getElementById('dialog').open) return;
    const active = document.activeElement;
    if (active.matches('input,textarea,select') || active.isContentEditable)
      return;
    const key = event.key;
    const group = groups.find(g => g.items.includes(allItems[focused]));
    const groupIndex = groups.indexOf(group);
    if (key === 'j' || key === 'k') focusItem(focused + (key === 'j' ? 1 : -1));
    else if (key === 'J' || key === 'K') {
      const target = groups[(groupIndex + (key === 'J' ? 1 : -1) +
        groups.length) % groups.length];
      if (target) focusItem(allItems.indexOf(target.items[0]));
    } else if (key === 'n') {
      for (let i = 1; i <= allItems.length; i++) {
        const index = (focused + i) % allItems.length;
        if (allScopes(allItems[index]).some(scope =>
            data.scopes[scope].definition.required &&
            state(scope) !== 'answered')) {
          focusItem(index); break;
        }
      }
    } else if (key === 'f') {
      focusMode = !focusMode;
      document.body.classList.toggle('focus-mode', focusMode);
    } else if (key === 'u') undo();
    else if (key === '?') showLegend();
    else if (key === '/') {
      const query = prompt('Filter item IDs or titles', filter);
      if (query !== null) {
        filter = query.toLowerCase();
        for (const card of document.querySelectorAll('.item')) {
          const item = allItems.find(i => i.id === card.dataset.itemId);
          card.classList.toggle('hidden', !((item.title || item.id)
            .toLowerCase().includes(filter)));
        }
      }
    } else {
      const item = allItems[focused];
      if (!item) return;
      for (const scope of activeScopes(item)) {
        const definition = data.scopes[scope].definition;
        if (definition.authority) continue;
        const options = definition.options || [];
        const keys = definition.keys ||
          (definition.kind === 'flags' ?
            options.slice(0, 9).map((_, i) => i + 1).join('') : '');
        const index = keys.indexOf(key);
        if (index >= 0 && index < options.length) {
          if (definition.kind === 'choice') setAnswer(scope, options[index]);
          if (definition.kind === 'flags') {
            const chosen = [...(currentValue(scope) || [])];
            const at = chosen.indexOf(options[index]);
            if (at < 0) chosen.push(options[index]);
            else chosen.splice(at, 1);
            setAnswer(scope, chosen);
          }
          event.preventDefault(); return;
        }
      }
      for (const entry of mediaViews.get(item.id) || []) {
        try {
          if (entry.component.key?.(event, entry.view, entry.api)) {
            event.preventDefault(); return;
          }
        } catch (error) {
          entry.fail('Component key failed: ' + error);
        }
      }
    }
  }
  window.ReviewSheet = {metrics() {
    return {decodedTiles: frameCache.size, decodedTileLimit: 32,
      cacheEvictions, decodedBytes: [...frameCache.values()].reduce(
        (total, entry) => total + entry.image.naturalWidth *
          entry.image.naturalHeight * 4, 0)};
  }, register(component) {
    const expected = data.components[component?.kind];
    if (!component || !expected || registry.has(component.kind) ||
        component.version !== expected.version ||
        component.api !== expected.api ||
        JSON.stringify(Object.keys(component.keys || {}).sort()) !==
          JSON.stringify([...expected.keys].sort()) ||
        typeof component.render !== 'function' ||
        ['focus', 'blur', 'key', 'showFrame', 'dispose'].some(name =>
          component[name] !== undefined &&
          typeof component[name] !== 'function')) {
      registryErrors.push('Missing, duplicate, or mismatched component: ' +
        (component?.kind || 'unknown'));
      return;
    }
    registry.set(component.kind, component);
  }, start() {
    publishHandles(); load(); prefill(); render();
    for (const kind of Object.keys(data.components))
      if (!registry.has(kind)) registryErrors.push(
        'Missing component registration: ' + kind);
    for (const error of registryErrors) note(error, true);
    if (registryErrors.length) renderStatus();
    document.addEventListener('keydown', keydown);
    window.addEventListener('pagehide', () => {
      pageDisposed = true;
      generation++;
      clearFrameCache();
      for (const entries of mediaViews.values()) for (const entry of entries) {
        if (entry.resources.disposed) continue;
        entry.resources.disposed = true;
        try { entry.component.blur?.(entry.view, entry.api); }
        catch (_) {}
        try { entry.component.dispose?.(entry.view, entry.api); }
        catch (_) {}
        for (const worker of entry.resources.workers) worker.terminate();
        for (const url of entry.resources.urls) URL.revokeObjectURL(url);
      }
      for (const handle of mediaHandles.values())
        handle.reject(Error('media disposed'));
      for (const resources of liveResources) {
        resources.disposed = true;
        for (const worker of resources.workers) worker.terminate();
        for (const url of resources.urls) URL.revokeObjectURL(url);
      }
    });
  }};
})();
