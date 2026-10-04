ReviewSheet.register({kind: 'text', version: '1.0.0',
  render(media, api) {
    const box = document.createElement('div');
    const source = document.createElement('small');
    source.textContent = 'Source: ' + media.src;
    const pre = document.createElement('pre');
    pre.textContent = new TextDecoder().decode(api.bytes || new Uint8Array());
    box.append(source, pre); return box;
  }});
