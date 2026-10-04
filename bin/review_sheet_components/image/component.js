ReviewSheet.register({kind: 'image', version: '1.0.0',
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
    compare.addEventListener('click', () => {
      if (other) { other.remove(); other = null; return; }
      const url = api.comparison();
      if (!url) { api.note('Comparison image is unavailable.'); return; }
      other = document.createElement('img');
      other.src = url; other.alt = 'Another image in this group';
      other.style.marginLeft = '.5rem';
      box.append(other);
    });
    box.append(compare);
    return box;
  }});
