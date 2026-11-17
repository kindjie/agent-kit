ReviewSheet.register({kind: 'image', version: '1.1.0', api: 1,
  keys: {z: 'zoom'}, render(media, api) {
    const box = document.createElement('div');
    box.className = 'image-view';
    const panels = document.createElement('div');
    panels.className = 'image-panels';
    const images = (api.item?.media || []).filter(row => row.kind === 'image');
    const position = images.findIndex(row => row.index === api.item?.index);
    const ownLabel = media.alt || `${position + 1} of ${images.length}`;
    let zoomed = false, other = null, cursor = -1, generation = 0;
    let disposed = false;
    function fit() {
      const nodes = [...panels.querySelectorAll('img')];
      if (nodes.some(node => !node.naturalWidth)) return;
      // One factor for both images preserves relative pixel scale and aspect.
      const maxWidth = Math.max(...nodes.map(node => node.naturalWidth));
      const maxHeight = Math.max(...nodes.map(node => node.naturalHeight));
      const width = Math.max(1, (box.clientWidth - (other ? 8 : 0)) /
        nodes.length);
      const scale = zoomed ? 1 : Math.min(1, width / maxWidth, 448 / maxHeight);
      for (const node of nodes) {
        node.style.width = node.naturalWidth * scale + 'px';
        node.style.maxWidth = zoomed || other ? 'none' : '';
        node.style.maxHeight = zoomed || other ? 'none' : '';
        node.parentElement.style.width = node.naturalWidth * scale + 'px';
      }
      box.classList.toggle('zoomed', zoomed);
    }
    function figure(url, label) {
      const panel = document.createElement('figure');
      const image = document.createElement('img');
      image.src = url; image.alt = label; image.tabIndex = 0;
      image.addEventListener('load', fit);
      image.addEventListener('click', () => { zoomed = !zoomed; fit(); });
      const caption = document.createElement('figcaption');
      caption.textContent = label;
      panel.append(image, caption);
      return panel;
    }
    panels.append(figure(api.url, ownLabel));
    box.append(panels);
    const siblings = (api.group?.members || []).filter(row =>
      row.id !== api.item?.id).flatMap(row => {
      const image = row.media?.find(entry => entry.kind === 'image');
      return image ? [{id: row.id, index: image.index,
        label: image.label || row.label}] : [];
    });
    // Explicit comparison groups keep their cross-item meaning. Ordinary
    // galleries prefer their own images, then fall back to group alternatives.
    const targets = api.group?.comparison ? siblings : images.length > 1 ?
      images.slice(position + 1).concat(images.slice(0, position)).map(row => ({
        id: api.item.id, index: row.index,
        label: row.label || `${images.indexOf(row) + 1} of ${images.length}`})) :
      siblings;
    if (targets.length) {
      const controls = document.createElement('div');
      controls.className = 'image-controls';
      const compare = document.createElement('button');
      compare.type = 'button'; compare.textContent = 'Compare';
      compare.setAttribute('aria-expanded', 'false');
      const close = document.createElement('button');
      close.type = 'button'; close.textContent = 'Close comparison';
      close.hidden = true;
      compare.addEventListener('click', async () => {
        const current = ++generation;
        const next = (cursor + 1) % targets.length;
        const target = targets[next];
        try {
          const handle = await api.media(target.id, target.index);
          const candidate = figure(handle.url, target.label);
          await candidate.querySelector('img').decode();
          if (disposed || current !== generation) return;
          other?.remove(); other = candidate; cursor = next;
          panels.append(other); close.hidden = false;
          compare.setAttribute('aria-expanded', 'true');
          fit();
        } catch (_) {
          if (!disposed && current === generation)
            api.note('Comparison image is unavailable.', true);
        }
      });
      close.addEventListener('click', () => {
        generation++; other?.remove(); other = null; cursor = -1;
        close.hidden = true; compare.setAttribute('aria-expanded', 'false');
        compare.focus({preventScroll: true}); fit();
      });
      controls.append(compare, close); box.append(controls);
    }
    const observer = new ResizeObserver(fit);
    observer.observe(box);
    box.disposeImage = () => {
      disposed = true; generation++; observer.disconnect();
    };
    return box;
  },
  key(event, box) {
    if (event.key !== 'z') return false;
    box.querySelector('img')?.click();
    return true;
  },
  dispose(box) { box.disposeImage(); }});
