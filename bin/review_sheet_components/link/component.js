ReviewSheet.register({kind: 'link', version: '1.0.0', api: 1,
  render(media, api) {
    const box = document.createElement('div');
    const text = document.createElement('code');
    text.textContent = media.src;
    box.append(text);
    if (api.settings.privacy === 'shareable') {
      const copy = document.createElement('button');
      copy.type = 'button'; copy.textContent = 'Copy link';
      copy.addEventListener('click', () =>
        navigator.clipboard.writeText(media.src).catch(() =>
          api.note('Clipboard unavailable.')));
      box.append(copy);
    }
    api.ready(); return box;
  }});
