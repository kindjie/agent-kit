ReviewSheet.register({kind: 'frame-sequence', version: '1.0.0', api: 1,
  render(media, api) {
    const box = document.createElement('div');
    box.className = 'frame-sequence';
    const image = document.createElement('img');
    image.alt = 'Numbered frame sequence';
    box.append(image);
    api.ready();
    return box;
  },
  async showFrame(index, box, api, signal) {
    if (signal?.aborted) throw Error('frame request cancelled');
    const frame = await api.frame(index, signal);
    if (signal?.aborted) throw Error('frame request cancelled');
    return {frame, commit() {
      box.querySelector('img').src = frame.url;
      box.dataset.frameIndex = String(index);
      box.dataset.sourceFrame = String(frame.source_frame);
    }};
  }});
