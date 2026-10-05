ReviewSheet.register({kind: 'audio', version: '1.0.0', api: 1,
  render(media, api) {
    const audio = document.createElement('audio');
    audio.src = api.url;
    audio.controls = true;
    audio.preload = 'metadata';
    audio.volume = api.settings.gain;
    audio.setAttribute('aria-label', media.alt || media.src);
    audio.addEventListener('volumechange', () => {
      if (audio.volume > api.settings.gainCap)
        audio.volume = api.settings.gainCap;
    });
    return audio;
  },
  blur(audio) { audio.pause(); },
  dispose(audio) { audio.pause(); audio.removeAttribute('src'); }});
