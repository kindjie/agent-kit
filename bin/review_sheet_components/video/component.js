ReviewSheet.register({kind: 'video', version: '1.0.0', api: 1,
  render(media, api) {
    const box = document.createElement('div');
    const video = document.createElement('video');
    video.src = api.url;
    video.controls = true;
    video.preload = 'metadata';
    video.setAttribute('aria-label', media.alt || media.src);
    const step = document.createElement('button');
    step.type = 'button'; step.textContent = 'Next frame';
    step.addEventListener('click', () => {
      video.pause(); video.currentTime += 1 / (media.fps || 30);
    });
    const loop = document.createElement('button');
    loop.type = 'button'; loop.textContent = 'Set loop start';
    let start = null;
    let end = null;
    loop.addEventListener('click', () => {
      if (start === null) {
        start = video.currentTime;
        loop.textContent = 'Set loop end';
      } else if (end === null) {
        end = video.currentTime;
        if (end <= start) { start = null; end = null;
          loop.textContent = 'Set loop start'; return; }
        loop.textContent = 'Clear loop';
      } else {
        start = null; end = null; loop.textContent = 'Set loop start';
      }
    });
    video.addEventListener('timeupdate', () => {
      if (end !== null && video.currentTime >= end) video.currentTime = start;
    });
    box.append(video, step, loop); return box;
  },
  blur(box) { box.querySelector('video')?.pause(); },
  dispose(box) {
    const video = box.querySelector('video');
    video.pause(); video.removeAttribute('src');
  }});
