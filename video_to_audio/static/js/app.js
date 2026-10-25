const $ = id => document.getElementById(id);
const urlInput = $('urlInput'), limitInput = $('limitMinutes'), goBtn = $('go');
const queueEl = $('queue'), emptyEl = $('empty'), summaryEl = $('summary');
const zipAllEl = $('zipAll'), clearDoneBtn = $('clearDone'), formError = $('formError');
const tpl = $('itemTpl');

// Every link added in this session: { key, url, fmt, limit, jobId, status, progress, title, error, el }
const items = [];
let nextKey = 1;

function parseLinks(text) {
  const found = text.match(/https?:\/\/[^\s,]+/g) || [];
  return [...new Set(found)];
}

function selectedFormat() {
  return document.querySelector('input[name="fmt"]:checked').value;
}

// Links the user has added but not started downloading yet.
const pending = [];

function refreshFormLabels() {
  const n = pending.length;
  $('linkCount').textContent = n === 1 ? '1 link' : `${n} links`;
  $('pendingEmpty').style.display = n ? 'none' : '';
  $('clearPending').disabled = !n;
  const what = selectedFormat() === 'mp4' ? 'MP4' : 'MP3';
  goBtn.textContent = n > 1 ? `Download all ${n} as ${what}` : `Download as ${what}`;
  goBtn.disabled = !n && !urlInput.value.trim();
}
urlInput.addEventListener('input', refreshFormLabels);
document.querySelectorAll('input[name="fmt"]').forEach(i => i.addEventListener('change', refreshFormLabels));

function renderPending() {
  const list = $('pending');
  list.replaceChildren();
  for (const url of pending) {
    const li = document.createElement('li');
    const text = document.createElement('span');
    text.className = 'url';
    text.textContent = url;
    text.title = url;
    li.append(text);
    if (isPlaylist(url)) {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = 'PLAYLIST';
      li.append(tag);
    }
    const remove = document.createElement('button');
    remove.className = 'icon-btn';
    remove.title = 'Remove';
    remove.setAttribute('aria-label', 'Remove');
    remove.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    remove.onclick = () => { pending.splice(pending.indexOf(url), 1); renderPending(); };
    li.append(remove);
    list.append(li);
  }
  list.scrollTop = list.scrollHeight;
  refreshFormLabels();
}

function addFromInput() {
  formError.textContent = '';
  // Pasting several links at once into the box still works.
  const links = parseLinks(urlInput.value);
  if (!links.length) {
    formError.textContent = urlInput.value.trim()
      ? 'That doesn\'t look like a link. It should start with http:// or https://'
      : 'Paste a link first.';
    urlInput.focus();
    return;
  }
  const fresh = links.filter(l => !pending.includes(l));
  if (!fresh.length) formError.textContent = 'That link is already in your list.';
  pending.push(...fresh);
  urlInput.value = '';
  urlInput.focus();
  renderPending();
}

urlInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); addFromInput(); }
});
$('addBtn').addEventListener('click', addFromInput);
$('clearPending').addEventListener('click', () => { pending.length = 0; renderPending(); });

// Playlist links: youtube.com/playlist?list=... or a video opened from a
// playlist (watch?v=...&list=...). "RD..." lists are YouTube's endless
// auto-generated Mixes, so those are treated as the single video.
function isPlaylist(url) {
  try {
    const u = new URL(url);
    const list = u.searchParams.get('list');
    return !!list && !list.startsWith('RD');
  } catch { return false; }
}

goBtn.addEventListener('click', () => {
  formError.textContent = '';
  // A link still sitting in the box (Enter not pressed yet) counts too.
  if (urlInput.value.trim()) addFromInput();
  if (!pending.length) {
    formError.textContent = 'Add at least one link first.';
    return;
  }
  const fmt = selectedFormat();
  const limit = limitInput.value.trim() || null;
  for (const url of pending) {
    if (isPlaylist(url)) addPlaylist(url, fmt, limit);
    else addVideo(url, fmt, limit);
  }
  pending.length = 0;
  renderPending();
  render();
});
renderPending();

function addVideo(url, fmt, limit, title) {
  const item = { key: nextKey++, url, fmt, limit, title, status: 'starting', progress: 0 };
  items.push(item);
  createRow(item);
  start(item);
  return item;
}

async function addPlaylist(url, fmt, limit) {
  // Placeholder row while the playlist is being read; replaced by its videos.
  const placeholder = { key: nextKey++, url, fmt, limit, status: 'expanding', progress: 0 };
  items.push(placeholder);
  createRow(placeholder);
  updateRow(placeholder);
  render();
  try {
    const res = await fetch('/expand', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not read this playlist.');
    if (!data.videos.length) throw new Error('This playlist has no downloadable videos.');
    if (!items.includes(placeholder)) return; // removed while loading
    removeItem(placeholder);
    // Prepend in reverse so the queue shows the playlist in its original order.
    for (const v of [...data.videos].reverse()) addVideo(v.url, fmt, limit, v.title);
    render();
  } catch (err) {
    // Retry on this row should re-read the playlist, not download one video.
    placeholder.retry = () => { removeItem(placeholder); addPlaylist(url, fmt, limit); };
    fail(placeholder, err.message);
  }
}

async function start(item) {
  item.status = 'starting';
  item.error = null;
  item.progress = 0;
  updateRow(item);
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: item.url, format: item.fmt, limit_minutes: item.limit })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Something went wrong.');
    item.jobId = data.job_id;
    item.status = 'queued';
    updateRow(item);
    poll(item);
  } catch (err) {
    fail(item, err.message);
  }
}

async function poll(item) {
  if (!items.includes(item)) return; // removed by the user
  try {
    const res = await fetch(`/status/${item.jobId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lost track of this download.');
    item.status = data.status;
    item.progress = data.progress || 0;
    if (data.title) item.title = data.title;
    if (data.status === 'error') return fail(item, data.error);
    updateRow(item);
    render();
    if (data.status !== 'done') setTimeout(() => poll(item), 1200);
  } catch (err) {
    fail(item, err.message === 'Failed to fetch' ? 'Lost connection to the server.' : err.message);
  }
}

function fail(item, message) {
  item.status = 'error';
  item.error = message;
  updateRow(item);
  render();
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

function createRow(item) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  item.el = el;
  queueEl.prepend(el);
}

function updateRow(item) {
  const el = item.el;
  el.className = 'item ' + item.status;
  el.querySelector('.icon').textContent = item.status === 'expanding' ? 'LIST' : item.fmt.toUpperCase();
  el.querySelector('.title').textContent = item.title || item.url;
  el.querySelector('.title').title = item.url;

  const line = el.querySelector('.line');
  const bar = el.querySelector('.bar');
  const fill = bar.querySelector('span');
  bar.classList.remove('indeterminate');

  const labels = {
    expanding: 'Reading playlist…',
    starting: 'Starting…',
    queued: 'Waiting for a free slot…',
    downloading: item.progress ? `Downloading… ${Math.floor(item.progress)}%` : 'Fetching video info…',
    processing: item.fmt === 'mp3' ? 'Converting to MP3…' : 'Merging video and audio…',
    done: `Ready · ${hostOf(item.url)}` + (item.hfRepo ? ` · On Hugging Face (${item.hfRepo})` : ''),
    error: item.error || 'Failed',
  };
  line.textContent = labels[item.status] || item.status;

  if (item.status === 'downloading' && item.progress) {
    fill.style.width = item.progress + '%';
  } else {
    fill.style.width = '';
    bar.classList.add('indeterminate');
  }

  const right = el.querySelector('.right');
  right.replaceChildren();
  if (item.status === 'done') {
    const a = document.createElement('a');
    a.className = 'btn';
    a.href = `/download/${item.jobId}`;
    a.textContent = 'Save';
    right.append(a);
  }
  if (item.status === 'error') {
    const retry = document.createElement('button');
    retry.className = 'small';
    retry.textContent = 'Retry';
    retry.onclick = () => (item.retry ? item.retry() : start(item));
    right.append(retry);
  }
  const remove = document.createElement('button');
  remove.className = 'icon-btn';
  remove.title = 'Remove from list';
  remove.setAttribute('aria-label', 'Remove from list');
  remove.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  remove.onclick = () => removeItem(item);
  right.append(remove);
}

function removeItem(item) {
  const i = items.indexOf(item);
  if (i >= 0) items.splice(i, 1);
  item.el.remove();
  render();
}

clearDoneBtn.addEventListener('click', () => {
  items.filter(i => i.status === 'done' || i.status === 'error').forEach(removeItem);
});

const retryFailedBtn = $('retryFailed');
retryFailedBtn.addEventListener('click', () => {
  items.filter(i => i.status === 'error').forEach(i => (i.retry ? i.retry() : start(i)));
  render();
});

function render() {
  emptyEl.style.display = items.length ? 'none' : '';
  const done = items.filter(i => i.status === 'done');
  const failed = items.filter(i => i.status === 'error').length;
  const active = items.length - done.length - failed;

  const parts = [];
  if (items.length) parts.push(`${done.length} of ${items.length} ready`);
  if (active) parts.push(`${active} in progress`);
  if (failed) parts.push(`${failed} failed`);
  summaryEl.textContent = parts.length ? '· ' + parts.join(' · ') : '';

  clearDoneBtn.disabled = done.length + failed === 0;
  retryFailedBtn.style.display = failed ? '' : 'none';
  retryFailedBtn.textContent = `Retry ${failed} failed`;
  if (done.length > 1) {
    zipAllEl.style.display = '';
    zipAllEl.textContent = `Download all ${done.length} (.zip)`;
    zipAllEl.href = '/download_zip?ids=' + done.map(i => i.jobId).join(',');
  } else {
    zipAllEl.style.display = 'none';
  }
  renderHf();
}

// --- Push to Hugging Face ---
const hfRepo = $('hfRepo'), hfToken = $('hfToken'), hfPrivate = $('hfPrivate');
const hfPushBtn = $('hfPush'), hfResult = $('hfResult');
let hfBusy = false;
try { hfRepo.value = localStorage.getItem('hfRepo') || ''; } catch {}
hfRepo.addEventListener('input', () => {
  try { localStorage.setItem('hfRepo', hfRepo.value.trim()); } catch {}
  renderHf();
});

// Finished MP3s not yet pushed to the dataset currently typed in.
function hfCandidates() {
  const repo = hfRepo.value.trim();
  return items.filter(i => i.status === 'done' && i.fmt === 'mp3' && i.hfRepo !== repo);
}

function renderHf() {
  if (hfBusy) return;
  const n = hfCandidates().length;
  const anyMp3 = items.some(i => i.status === 'done' && i.fmt === 'mp3');
  hfPushBtn.disabled = !n || !hfRepo.value.trim();
  hfPushBtn.textContent = n ? `Push ${n} MP3${n > 1 ? 's' : ''} to Hugging Face`
    : anyMp3 ? 'All MP3s pushed to this dataset' : 'Push MP3s to Hugging Face';
}

hfPushBtn.addEventListener('click', async () => {
  const repo = hfRepo.value.trim();
  const batch = hfCandidates();
  hfBusy = true;
  hfPushBtn.disabled = true;
  hfPushBtn.textContent = `Uploading ${batch.length}…`;
  hfResult.className = '';
  hfResult.textContent = '';
  try {
    const res = await fetch('/push_hf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_id: repo, token: hfToken.value.trim(), private: hfPrivate.checked,
                             ids: batch.map(i => i.jobId) })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed.');
    batch.forEach(i => { i.hfRepo = repo; updateRow(i); });
    hfResult.className = 'ok';
    hfResult.textContent = `Pushed ${data.count} file${data.count > 1 ? 's' : ''} as ${data.parquet}. `;
    const a = document.createElement('a');
    a.href = data.repo_url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = 'Open dataset ↗';
    hfResult.append(a);
  } catch (err) {
    hfResult.className = 'err';
    hfResult.textContent = err.message === 'Failed to fetch' ? 'Lost connection to the server.' : err.message;
  } finally {
    hfBusy = false;
    renderHf();
  }
});
render();
