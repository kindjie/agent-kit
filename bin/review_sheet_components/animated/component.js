ReviewSheet.register({kind: 'animated', version: '1.0.0', api: 1,
  render(media, api) {
    const box = document.createElement('div');
    const image = document.createElement('img');
    image.src = api.url;
    image.alt = media.alt || media.src;
    if (api.settings.reducedMotion) image.title =
      'Animated media; reduced motion preference detected';
    const placeholder = document.createElement('button');
    placeholder.type = 'button';
    placeholder.textContent = 'Focus item to animate';
    placeholder.addEventListener('click', () => {
      if (box.dataset.focused !== 'true') return;
      image.style.display = '';
      image.src = api.url;
      placeholder.style.display = 'none';
    });
    box.append(image, placeholder);
    return box;
  },
  focus(box, api) {
    box.dataset.focused = 'true';
    const image = box.querySelector('img');
    if (api.settings.reducedMotion) {
      image.style.display = 'none';
      const control = box.querySelector('button');
      control.textContent = 'Play animation';
      control.style.display = '';
      return;
    }
    image.style.display = '';
    image.src = api.url;
    box.querySelector('button').style.display = 'none';
  },
  blur(box) {
    box.dataset.focused = 'false';
    box.querySelector('img').style.display = 'none';
    const control = box.querySelector('button');
    control.textContent = 'Focus item to animate';
    control.style.display = '';
  }});
