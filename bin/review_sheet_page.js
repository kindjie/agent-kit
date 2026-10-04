(function () {
  'use strict';
  const data = window.REVIEW_SHEET_DATA;
  const desc = data.description;
  const registry = new Map();
  const allItems = [...(desc.items || []),
    ...(desc.groups || []).flatMap(group => group.items)];
  const groups = desc.groups || [];
  const scopeKeys = Object.keys(data.scopes);
  const storageKey = 'review-sheet:' + desc.review;
  const answers = {};
  const mediaState = new Map();
  const mediaViews = new Map();
  const history = [];
  const pendingNotes = [];
  let focused = 0;
  let storageSafe = true;
  let loadedCount = 0;
  let filter = '';
  let focusMode = false;
  let reviewerValue = '';

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
      const entry = mediaState.get(src);
      return entry && entry.ready && entry.hash === scope.media[src];
    });
  }
  function itemReady(item) {
    return (item.media || []).every(media => {
      if (media.kind === 'link') return true;
      const state = mediaState.get(media.src);
      return state && state.ready;
    });
  }
  function blocked(key) {
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
    return answer.state;
  }
  function currentValue(key) {
    return state(key) === 'answered' ? answers[key].value : undefined;
  }
  function save() {
    if (!storageSafe) return;
    try {
      const serialized = JSON.stringify({schema_version: 1,
        review: desc.review, reviewer: reviewerValue, answers});
      localStorage.setItem(storageKey, serialized);
    } catch (_) {
      note('Draft could not be saved. Export your results now.', true);
      storageSafe = false;
    }
  }
  function load() {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return;
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
    if (data.scopes[key].authority) {
      if (!mediaFor(key)) {
        note('Embedded media hash has not been verified.', true);
        return;
      }
      answer.media_hashes = {...data.scopes[key].media};
    } else answer.evidence = 'unverified';
    history.push([key, answers[key] ? structuredClone(answers[key]) : null]);
    if (history.length > 100) history.shift();
    answers[key] = answer;
    save();
    renderStatus();
  }
  function undo() {
    const step = history.pop();
    if (!step) return;
    if (step[1] === null) delete answers[step[0]];
    else answers[step[0]] = step[1];
    save();
    renderStatus();
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
        const control = button((hotkey ? hotkey + ' · ' : '') + String(value),
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
  async function renderMedia(media, parent, item) {
    const shell = el('div', 'media');
    parent.append(shell);
    const component = registry.get(media.kind);
    if (!component) {
      shell.append(el('p', 'error', 'Unknown media component'));
      return;
    }
    const api = {settings: {gain: 0.15, gainCap: 0.5,
      privacy: desc.privacy,
      reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches},
      note: message => note(message), bytes: null, url: null,
      comparison: () => {
        const group = groups.find(g => g.items.includes(item));
        if (!group) return null;
        for (const other of group.items) {
          if (other === item) continue;
          const otherMedia = (other.media || []).find(m => m.kind === 'image');
          if (!otherMedia) continue;
          const state = mediaState.get(otherMedia.src);
          if (state && state.ready) return state.url || otherMedia.src;
        }
        return null;
      }};
    if (media.mode === 'embed') {
      try {
        const raw = bytes(media.data);
        const hash = await sha256(raw);
        if (hash !== media.sha256) throw Error('embedded hash mismatch');
        api.bytes = raw;
        api.url = URL.createObjectURL(new Blob([raw], {type: media.mime}));
        mediaState.set(media.src, {ready: true, hash, url: api.url});
      } catch (_) {
        mediaState.set(media.src, {ready: false});
        shell.append(el('p', 'error', 'Embedded media verification failed.'));
        return;
      }
    } else if (media.kind !== 'link') {
      api.url = media.src;
      mediaState.set(media.src, {ready: false, hash: null,
        url: media.src});
    }
    const view = component.render(media, api);
    const display = view.matches('img,audio,video') ? view :
      view.querySelector('img,audio,video');
    if (media.mode === 'reference') {
      if (display) display.addEventListener(
        display.tagName === 'IMG' ? 'load' : 'loadedmetadata', () => {
          mediaState.set(media.src, {ready: true, hash: null,
            url: media.src});
          renderStatus();
        }, {once: true});
    }
    display?.addEventListener('error', () => {
      mediaState.set(media.src, {ready: false});
      shell.append(el('p', 'error',
        'Media could not load. Decisions disabled.'));
      renderStatus();
    });
    shell.append(view);
    if (!mediaViews.has(item.id)) mediaViews.set(item.id, []);
    mediaViews.get(item.id).push({component, view, api});
    if (allItems[focused] === item) component.focus?.(view, api);
    else component.blur?.(view, api);
    renderStatus();
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
    for (const media of item.media || []) renderMedia(media, card, item);
    for (const key of allScopes(item)) card.append(decision(key));
    card.addEventListener('click', () => focusItem(allItems.indexOf(item)));
    return card;
  }
  function focusItem(index) {
    if (!allItems.length) return;
    const previous = document.querySelector('.item.focused');
    if (previous) {
      previous.classList.remove('focused');
      for (const node of previous.querySelectorAll('audio,video')) node.pause();
      for (const entry of mediaViews.get(previous.dataset.itemId) || [])
        entry.component.blur?.(entry.view, entry.api);
    }
    focused = (index + allItems.length) % allItems.length;
    const card = [...document.querySelectorAll('.item')].find(node =>
      node.dataset.itemId === allItems[focused].id);
    if (card) {
      card.classList.add('focused');
      for (const entry of mediaViews.get(card.dataset.itemId) || [])
        entry.component.focus?.(entry.view, entry.api);
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
      const list = el('div', group.layout === 'grid' ? 'grid' : 'list');
      group.items.forEach(item => list.append(renderItem(item)));
      section.append(list); app.append(section);
    }
    (desc.items || []).forEach(item => app.append(renderItem(item)));
    const dialog = el('dialog'); dialog.id = 'dialog'; app.append(dialog);
    renderStatus(); focusItem(0);
    note('Exports download as review-results.json. Move private results to '
      + 'the private review directory before import.');
  }
  function packet() {
    const exported = {};
    for (const key of scopeKeys) {
      const answer = answers[key];
      exported[key] = answer ? {...answer, state: state(key)} :
        {state: 'unanswered', digest: data.scopes[key].digest};
    }
    const result = {schema_version: 1, review: desc.review,
      resolved_sha256: data.resolved_sha256,
      context: desc.context || {}, reviewer: document.getElementById(
        'reviewer').value, answers: exported};
    return result;
  }
  function exportResults() {
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
    if (packet.schema_version !== 1 || packet.review !== desc.review ||
        !packet.answers ||
        typeof packet.answers !== 'object') {
      note('Results are for a different review or malformed.', true); return;
    }
    if (typeof packet.reviewer === 'string' &&
        !document.getElementById('reviewer').value) {
      document.getElementById('reviewer').value = packet.reviewer;
      reviewerValue = packet.reviewer;
    }
    const conflicts = [];
    for (const [key, incoming] of Object.entries(packet.answers)) {
      if (!data.scopes[key] || !incoming || incoming.state !== 'answered')
        continue;
      const existing = answers[key];
      if (existing && existing.state === 'answered' &&
          JSON.stringify(existing.value) !== JSON.stringify(incoming.value)) {
        conflicts.push([key, existing, incoming]);
      } else answers[key] = incoming;
    }
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
  function showLegend() {
    const dialog = document.getElementById('dialog');
    dialog.replaceChildren(el('h2', '', 'Keyboard controls'),
      el('p', '', 'j/k item · J/K group · n next unanswered · f focus mode '
        + '· / filter · u undo · ? legend. Choice and flag keys appear '
        + 'beside their options. Authority decisions require confirmation.'),
      button('Close', () => dialog.close()));
    dialog.showModal();
  }
  function keydown(event) {
    if (document.getElementById('dialog').open) return;
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
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
    }
  }
  window.ReviewSheet = {register(component) {
    if (!component || !component.kind || registry.has(component.kind))
      throw Error('invalid or duplicate component');
    registry.set(component.kind, component);
  }, start() {
    load(); prefill(); render();
    document.addEventListener('keydown', keydown);
  }};
})();
