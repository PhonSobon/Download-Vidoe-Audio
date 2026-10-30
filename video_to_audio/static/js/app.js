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

// How many videos to take from each playlist ('' = all), remembered between visits.
const playlistLimit = $('playlistLimit'), formNote = $('formNote');
try {
  const saved = localStorage.getItem('playlistLimit');
  if (saved !== null && [...playlistLimit.options].some(o => o.value === saved)) playlistLimit.value = saved;
} catch {}
playlistLimit.addEventListener('change', () => {
  try { localStorage.setItem('playlistLimit', playlistLimit.value); } catch {}
});

goBtn.addEventListener('click', () => {
  formError.textContent = '';
  formNote.textContent = '';
  // A link still sitting in the box (Enter not pressed yet) counts too.
  if (urlInput.value.trim()) addFromInput();
  if (!pending.length) {
    formError.textContent = 'Add at least one link first.';
    return;
  }
  const fmt = selectedFormat();
  const limit = limitInput.value.trim() || null;
  const maxVideos = playlistLimit.value || null;
  let skipped = 0;
  for (const url of pending) {
    if (isPlaylist(url)) addPlaylist(url, fmt, limit, maxVideos);
    else if (!addVideo(url, fmt, limit)) skipped++;
  }
  noteSkipped(skipped);
  pending.length = 0;
  renderPending();
  render();
});
renderPending();

// Same video whatever form its link takes: watch?v=ID, youtu.be/ID, shorts/ID,
// with or without &t=, &list= etc. Other sites fall back to the link itself.
function videoId(url) {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^(www|m|music)\./, '');
    if (host === 'youtu.be') return 'yt:' + u.pathname.slice(1).split('/')[0];
    if (host === 'youtube.com') {
      if (u.searchParams.get('v')) return 'yt:' + u.searchParams.get('v');
      const m = u.pathname.match(/^\/(shorts|live|embed)\/([^/]+)/);
      if (m) return 'yt:' + m[2];
    }
    u.hash = '';
    return u.href;
  } catch { return url; }
}

// Returns the new queue item, or null if this video is already in the queue
// in the same format.
// `video` is what's already known about it from its playlist:
// { title, index, thumbnail, duration, channel }.
// `playlist` is "Playlist - Channel" for the playlist the video came from, if any.
function addVideo(url, fmt, limit, video = {}, playlist) {
  const id = videoId(url);
  if (items.some(i => i.videoId === id && i.fmt === fmt)) return null;
  const { title, index, duration, channel } = video;
  // YouTube thumbnails can be shown straight away, before the download starts.
  const thumbnail = video.thumbnail || (id.startsWith('yt:') ? `https://i.ytimg.com/vi/${id.slice(3)}/mqdefault.jpg` : null);
  const item = { key: nextKey++, url, videoId: id, fmt, limit, title, index, thumbnail, duration, channel,
                 playlist, status: 'starting', progress: 0 };
  items.push(item);
  createRow(item);
  start(item);
  return item;
}

// Playlists that still have videos left to fetch, shown as a "continue" bar
// above the queue: { url, fmt, limit, title, channel, total, added, next, busy, el }
const playlists = [];
const moreEl = $('playlistMore');

function addPlaylist(url, fmt, limit, maxVideos) {
  const pl = { url, fmt, limit, title: null, channel: null, total: null, added: 0, next: 1, busy: false };
  loadPlaylist(pl, maxVideos);
}

// Fetch the next `count` videos of a playlist (null = all that are left) and queue them.
async function loadPlaylist(pl, count) {
  // Placeholder row while the playlist is being read; replaced by its videos.
  const placeholder = { key: nextKey++, url: pl.url, fmt: pl.fmt, limit: pl.limit, title: pl.title, status: 'expanding', progress: 0 };
  items.push(placeholder);
  createRow(placeholder);
  updateRow(placeholder);
  pl.busy = true;
  renderMore();
  render();
  try {
    const res = await fetch('/expand', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: pl.url, limit: count, start: pl.next })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not read this playlist.');
    if (!items.includes(placeholder)) return; // removed while loading
    removeItem(placeholder);
    pl.title = data.title || pl.title;
    pl.channel = data.channel || pl.channel;
    // "Playlist - Channel", used to name the zip of its files.
    const zipName = [pl.title, pl.channel].filter(Boolean).join(' - ');
    pl.total = data.total;
    pl.next = data.next_start;
    pl.added += data.videos.length;
    if (!pl.added && !data.has_more) throw new Error('This playlist has no downloadable videos.');
    // Prepend in reverse so the queue shows the playlist in its original order.
    let skipped = 0;
    for (const v of [...data.videos].reverse()) {
      if (!addVideo(v.url, pl.fmt, pl.limit, v, zipName)) skipped++;
    }
    noteSkipped(skipped);
    if (data.has_more && !playlists.includes(pl)) playlists.push(pl);
    if (!data.has_more) dropPlaylist(pl);
    render();
  } catch (err) {
    // Retry on this row should re-read the playlist, not download one video.
    placeholder.retry = () => { removeItem(placeholder); loadPlaylist(pl, count); };
    fail(placeholder, err.message);
  } finally {
    pl.busy = false;
    renderMore();
  }
}

function noteSkipped(n) {
  if (n) formNote.textContent = `Skipped ${n} video${n > 1 ? 's' : ''} already in the queue.`;
}

function dropPlaylist(pl) {
  const i = playlists.indexOf(pl);
  if (i >= 0) playlists.splice(i, 1);
}

function renderMore() {
  moreEl.replaceChildren();
  for (const pl of playlists) {
    const row = document.createElement('div');
    row.className = 'more-row';
    const text = document.createElement('div');
    text.className = 'more-text';
    const name = document.createElement('b');
    name.textContent = pl.title || pl.url;
    name.title = pl.url;
    const left = pl.total ? pl.total - pl.next + 1 : null;
    const info = document.createElement('span');
    info.textContent = pl.total
      ? `Added ${pl.next - 1} of ${pl.total} videos · ${left} left`
      : `Added ${pl.next - 1} videos · more available`;
    text.append(name, info);

    const btns = document.createElement('div');
    btns.className = 'more-btns';
    const sizes = [10, 20, 50].filter(n => left === null || n < left);
    for (const n of sizes) btns.append(moreButton(pl, `+${n} more`, n));
    btns.append(moreButton(pl, left ? `All ${left} left` : 'All the rest', null));
    const close = document.createElement('button');
    close.className = 'icon-btn';
    close.title = 'Stop offering more from this playlist';
    close.setAttribute('aria-label', close.title);
    close.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    close.onclick = () => { dropPlaylist(pl); renderMore(); };
    btns.append(close);

    row.append(text, btns);
    moreEl.append(row);
  }
}

function moreButton(pl, label, count) {
  const b = document.createElement('button');
  b.className = 'small';
  b.textContent = pl.busy ? 'Loading…' : label;
  b.disabled = pl.busy;
  b.onclick = () => loadPlaylist(pl, count);
  return b;
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
    // Fill in whatever the playlist didn't tell us (always the case for single links).
    const card = data.card || {};
    for (const k of ['thumbnail', 'duration', 'channel']) if (card[k] && !item[k]) item[k] = card[k];
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

// 56:35, 1:02:03
function formatDuration(seconds) {
  if (!seconds) return '';
  seconds = Math.round(seconds);
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
  const pad = n => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function createRow(item) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  item.el = el;
  // A thumbnail that fails to load falls back to the plain format box.
  const img = el.querySelector('.thumb img');
  img.onerror = () => { img.removeAttribute('src'); el.querySelector('.thumb').classList.add('no-img'); };
  queueEl.prepend(el);
}

function updateRow(item) {
  const el = item.el;
  el.className = 'item ' + item.status;
  el.querySelector('.index').textContent = item.index || '';
  const thumb = el.querySelector('.thumb'), img = thumb.querySelector('img');
  if (item.thumbnail && img.dataset.src !== item.thumbnail) {
    img.dataset.src = item.thumbnail;
    img.src = item.thumbnail;
    thumb.classList.remove('no-img');
  }
  if (!img.hasAttribute('src')) thumb.classList.add('no-img');
  thumb.querySelector('.fmt').textContent = item.status === 'expanding' ? 'LIST' : item.fmt.toUpperCase();
  thumb.querySelector('.duration').textContent = formatDuration(item.duration);
  el.querySelector('.title').textContent = item.title || item.url;
  el.querySelector('.title').title = item.title || item.url;
  el.querySelector('.channel').textContent = item.channel || '';

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
    // The server deletes files a while after they're ready; if this one is
    // gone, show that (Retry downloads it again) instead of a JSON error page.
    a.onclick = async e => {
      e.preventDefault();
      try {
        const data = await (await fetch(`/status/${item.jobId}`)).json();
        if (data.status === 'expired') return fail(item, data.error);
      } catch {}
      location.href = a.href;
    };
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
    // Name the zip after the playlist and its channel when every file came
    // from the same playlist.
    const lists = new Set(done.map(i => i.playlist || ''));
    const [playlist] = lists;
    if (lists.size === 1 && playlist) zipAllEl.href += '&name=' + encodeURIComponent(playlist);
  } else {
    zipAllEl.style.display = 'none';
  }
  renderHf();
}

// --- YouTube sign-in (cookies) ---
const cookieStatus = $('cookieStatus'), cookieFile = $('cookieFile');
const cookieUploadBtn = $('cookieUpload'), cookieRemoveBtn = $('cookieRemove'), cookieResult = $('cookieResult');

function renderCookies(s) {
  cookieStatus.className = 'cookie-status';
  cookieRemoveBtn.style.display = s.source === 'file' ? '' : 'none';
  cookieUploadBtn.textContent = s.source === 'file' ? 'Replace cookies.txt' : 'Upload cookies.txt';
  if (s.source === 'file') {
    if (s.error) {
      cookieStatus.classList.add('warn');
      cookieStatus.textContent = `The saved cookies.txt can't be used: ${s.error}`;
    } else if (s.logged_in) {
      cookieStatus.classList.add('ok');
      cookieStatus.textContent = `✓ Signed-in YouTube cookies in use (uploaded ${s.updated}).`;
    } else {
      cookieStatus.classList.add('warn');
      cookieStatus.textContent = s.expired
        ? 'The saved cookies.txt has expired. Export a fresh one and upload it.'
        : 'The saved cookies.txt isn\'t logged in to YouTube, so it won\'t get past the bot check. Log in first, then export again.';
    }
  } else if (s.source === 'browser') {
    cookieStatus.textContent = `Reading cookies from ${s.browser} (COOKIES_FROM_BROWSER). An uploaded cookies.txt takes priority.`;
  } else {
    cookieStatus.textContent = 'No cookies yet. Downloads work without them until YouTube asks you to sign in.';
  }
}

async function cookieRequest(method, body) {
  cookieResult.textContent = '';
  try {
    const res = await fetch('/cookies', { method, body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Something went wrong.');
    renderCookies(data);
    return true;
  } catch (err) {
    cookieResult.textContent = err.message === 'Failed to fetch' ? 'Lost connection to the server.' : err.message;
    return false;
  }
}

cookieUploadBtn.addEventListener('click', () => cookieFile.click());
cookieFile.addEventListener('change', async () => {
  const file = cookieFile.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  cookieUploadBtn.disabled = true;
  const ok = await cookieRequest('POST', form);
  cookieUploadBtn.disabled = false;
  cookieFile.value = '';
  // Downloads that failed on the bot check can go again now.
  if (ok && items.some(i => i.status === 'error')) {
    cookieStatus.textContent += ' Press "Retry failed" in the queue to try again.';
  }
});
cookieRemoveBtn.addEventListener('click', () => cookieRequest('DELETE'));
cookieRequest('GET');

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
