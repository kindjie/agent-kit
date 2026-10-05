ReviewSheet.register({kind: 'image', version: '1.0.0', api: 1,
  keys: {z: 'zoom'}, render(media, api) {
    const box = document.createElement('div');
    box.style.overflow = 'auto';
    const image = document.createElement('img');
    image.src = api.url;
    image.alt = media.alt || media.src;
    image.addEventListener('click', () => {
      image.style.maxHeight = image.style.maxHeight ? '' : 'none';
      image.style.maxWidth = image.style.maxWidth ? '' : 'none';
    });
    box.append(image);
    const compare = document.createElement('button');
    compare.type = 'button'; compare.textContent = 'Compare';
    let other = null;
    compare.addEventListener('click', async () => {
      if (other) { other.remove(); other = null; return; }
      const sibling = api.group?.members.find(row =>
        row.id !== api.group.focused);
      let url;
      try { url = (await api.media(sibling.id, 0)).url; }
      catch (_) { api.note('Comparison image is unavailable.'); return; }
      other = document.createElement('img');
      other.src = url; other.alt = 'Another image in this group';
      other.style.marginLeft = '.5rem';
      box.append(other);
    });
    box.append(compare);
    return box;
  },
  key(event, box) {
    if (event.key !== 'z') return false;
    box.querySelector('img')?.click();
    return true;
  }});
