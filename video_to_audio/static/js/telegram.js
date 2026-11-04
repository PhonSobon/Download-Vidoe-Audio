// Telegram: paste a channel link, and every voice message in it is
// downloaded into a folder on this computer (telegram.py). A link sent to the
// Telegram bot starts the same download, and shows up here too.
const tgLink = $('tgLink'), tgGo = $('tgGo'), tgError = $('tgError');
let tgCurrent = null, tgTimer = null;
try { tgLink.value = localStorage.getItem('tgLink') || ''; } catch {}

const TG_ACTIVE = ['starting', 'finding', 'scanning', 'saving'];

async function tgRequest(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}

function tgShowError(err) {
  tgError.textContent = !err ? '' : err.message === 'Failed to fetch' ? 'Lost connection to the server.' : err.message;
}

$('tgForm').addEventListener('submit', async e => {
  e.preventDefault();
  const link = tgLink.value.trim();
  try { localStorage.setItem('tgLink', link); } catch {}
  tgShowError();
  tgGo.disabled = true;
  try {
    showTg(await tgRequest('/telegram/voice-downloads', { link }));
  } catch (err) {
    tgShowError(err);
    tgGo.disabled = false;
  }
});

$('tgStop').addEventListener('click', async () => {
  if (!tgCurrent) return;
  $('tgStop').disabled = true;
  try { await tgRequest(`/telegram/scans/${tgCurrent.id}/cancel`, {}); } catch (err) { tgShowError(err); }
});

function tgNumber(n) { return (n || 0).toLocaleString(); }

function showTg({ scan, bot }) {
  renderTgBot(bot);
  tgCurrent = scan;
  const active = !!scan && TG_ACTIVE.includes(scan.status);
  tgGo.disabled = active;
  tgGo.textContent = active ? 'Downloading…' : 'Download voice messages';
  $('tgProgress').hidden = !scan;
  if (!scan) return;

  const have = scan.saved + scan.already;
  const total = scan.end ? scan.start - scan.end + 1 : 0;
  $('tgChat').textContent = scan.chat_title || `t.me/${scan.chat}`;
  $('tgState').textContent = {
    starting: 'Starting…',
    finding: 'Finding the newest message…',
    scanning: 'Looking for voice messages…',
    saving: 'Finishing the last downloads…',
    done: 'Finished',
    cancelled: 'Stopped',
    error: 'Stopped with an error',
  }[scan.status] || scan.status;
  $('tgBar').style.width = (scan.status === 'done' ? 100 : total ? Math.min(100, scan.scanned * 100 / total) : 0) + '%';
  $('tgBar').parentElement.classList.toggle('done', !active);
  $('tgSaved').textContent = tgNumber(have);
  $('tgChecked').textContent = tgNumber(scan.scanned);
  $('tgCheckedLabel').textContent = total ? `of ${tgNumber(total)} messages checked` : 'messages checked';
  $('tgFailed').textContent = tgNumber(scan.failed);
  $('tgFailedBox').hidden = !scan.failed;
  $('tgStop').hidden = !active;
  $('tgStop').disabled = false;

  const zip = $('tgZip');
  zip.hidden = !have;
  zip.textContent = active ? `Download the ${tgNumber(have)} so far (.zip)` : `Download all ${tgNumber(have)} (.zip)`;
  zip.href = `/telegram/voice-downloads/${scan.id}/zip`;
  $('tgFolder').textContent = have || active ? `Also saved on this computer in ${scan.folder}` : '';

  if (scan.error) tgError.textContent = scan.error;
  else if (!active && !have) tgError.textContent = 'No voice messages in this chat.';
  else if (active) tgError.textContent = '';

  // The latest voice messages found, newest first.
  const ol = document.createElement('ol');
  for (const v of [...scan.voices].reverse()) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = v.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = `${v.author || 'Voice message'} · ${formatDuration(v.duration) || '?'}`;
    const meta = document.createElement('small');
    meta.textContent = [`#${v.post_id}`, (v.date || '').replace('T', ' ').slice(0, 16), v.text].filter(Boolean).join(' · ');
    li.append(a, meta);
    ol.append(li);
  }
  $('tgRecent').replaceChildren(...(scan.voices.length ? [ol] : []));
  $('tgRecent').hidden = !scan.voices.length;
}

function renderTgBot(bot) {
  const el = $('tgBot');
  el.replaceChildren();
  if (!bot || !bot.enabled) return;
  if (bot.error) { el.textContent = `Telegram bot: ${bot.error}`; return; }
  if (!bot.username) return;
  const a = document.createElement('a');
  a.href = `https://t.me/${bot.username}`;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = '@' + bot.username;
  el.append('You can also send the link to your bot ', a, ' in Telegram: the download shows up here.');
  if (!bot.owner) el.append(' The first person to message the bot becomes its owner.');
}

// Keep the page up to date: fast while downloading, slowly otherwise (to
// notice a download the bot started).
async function tgTick() {
  clearTimeout(tgTimer);
  const active = tgCurrent && TG_ACTIVE.includes(tgCurrent.status);
  if (!document.hidden && (active || location.hash === '#telegram')) {
    try { showTg(await tgRequest('/telegram/voice-downloads/latest')); } catch {}
  }
  tgTimer = setTimeout(tgTick, tgCurrent && TG_ACTIVE.includes(tgCurrent.status) ? 1500 : 4000);
}

window.addEventListener('hashchange', tgTick);
document.addEventListener('visibilitychange', () => { if (!document.hidden) tgTick(); });
tgRequest('/telegram/voice-downloads/latest').then(showTg).catch(() => {}).finally(() => {
  tgTimer = setTimeout(tgTick, 1500);
});
