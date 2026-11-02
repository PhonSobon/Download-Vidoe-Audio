// Saved link collections feed the existing downloader's pending-link list.
let currentCollection = null;
let collectionPoll = null;
const collectionHistory = $('collectionHistory');
const collectBtn = $('collectBtn'), cancelCollection = $('cancelCollection');
const collectorStatus = $('collectorStatus'), collectorErrors = $('collectorErrors');

async function collectionRequest(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}

function showCollection(data) {
  currentCollection = data.id;
  const active = ['queued', 'running'].includes(data.status);
  collectBtn.disabled = active;
  cancelCollection.hidden = !active;
  collectionHistory.disabled = active;
  const labels = { queued: 'Waiting to start', running: 'Collecting', done: 'Finished',
    partial: 'Finished with errors', error: 'Collection failed', cancelled: 'Cancelled',
    interrupted: 'Interrupted by a server restart — collect again to retry' };
  collectorStatus.textContent = `${labels[data.status] || data.status} · ${data.count} unique links · ` +
    `${data.scanned} posts inspected · ${data.profiles_done}/${data.options.profiles.length} creators processed`;
  collectorErrors.replaceChildren();
  for (const error of data.errors) {
    const p = document.createElement('p');
    p.textContent = [error.profile, error.error].filter(Boolean).join(': ');
    collectorErrors.append(p);
  }
  $('collectionResults').hidden = !data.count;
  $('exportCollection').href = `/collections/${data.id}/export.csv`;
  const preview = document.createElement('ol');
  for (const post of data.preview || []) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = post.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = post.title || post.url;
    const meta = document.createElement('small');
    meta.textContent = [post.creator, post.upload_date, post.topic].filter(Boolean).join(' · ');
    li.append(a, meta);
    preview.append(li);
  }
  $('collectorPreview').replaceChildren(preview);
  return active;
}

async function loadCollection(id, restoreForm = false) {
  clearTimeout(collectionPoll);
  try {
    const data = await collectionRequest(`/collections/${id}`);
    if (restoreForm) fillCollectionForm(data.options);
    const active = showCollection(data);
    if (active) collectionPoll = setTimeout(() => loadCollection(id), 1500);
    else await refreshCollectionHistory(id);
  } catch (error) {
    collectorErrors.textContent = error.message;
    collectBtn.disabled = false;
    cancelCollection.hidden = true;
    collectionHistory.disabled = false;
  }
}

async function refreshCollectionHistory(selectedId) {
  const collections = await collectionRequest('/collections');
  collectionHistory.replaceChildren();
  if (!collections.length) {
    collectionHistory.add(new Option('No saved collections yet', ''));
    return null;
  }
  for (const collection of collections) {
    const label = `${collection.created_at.replace('T', ' ').slice(0, 19)} · ` +
      `${collection.options.topic || collection.options.profiles[0]} · ${collection.count} links (${collection.status})`;
    collectionHistory.add(new Option(label, collection.id));
  }
  if (selectedId && collections.some(collection => collection.id === selectedId)) collectionHistory.value = selectedId;
  return collectionHistory.value;
}

$('collectorForm').addEventListener('submit', async event => {
  event.preventDefault();
  clearTimeout(collectionPoll);
  collectBtn.disabled = true;
  collectorErrors.textContent = '';
  collectorStatus.textContent = 'Starting collection…';
  try {
    const data = await collectionRequest('/collections', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profiles: $('creatorProfiles').value, max_posts: $('collectorLimit').value,
        topic: $('collectorTopic').value, keywords: $('collectorKeywords').value,
        date_from: $('collectorFrom').value, date_to: $('collectorTo').value })
    });
    showCollection(data);
    await refreshCollectionHistory(data.id);
    await loadCollection(data.id);
  } catch (error) {
    collectorStatus.textContent = '';
    collectorErrors.textContent = error.message;
    collectBtn.disabled = false;
  }
});

cancelCollection.addEventListener('click', async () => {
  if (!currentCollection) return;
  cancelCollection.disabled = true;
  try {
    await collectionRequest(`/collections/${currentCollection}/cancel`, { method: 'POST' });
    await loadCollection(currentCollection);
  } catch (error) {
    collectorErrors.textContent = error.message;
  } finally {
    cancelCollection.disabled = false;
  }
});

function fillCollectionForm(options) {
  $('creatorProfiles').value = options.profiles.join('\n');
  $('collectorLimit').value = options.max_posts;
  $('collectorTopic').value = options.topic;
  $('collectorKeywords').value = options.keywords.join(', ');
  for (const [field, id] of [['date_from', 'collectorFrom'], ['date_to', 'collectorTo']]) {
    $(id).value = options[field].replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
  }
}

collectionHistory.addEventListener('change', () => {
  if (collectionHistory.value) loadCollection(collectionHistory.value, true);
});

function useCollectedLinks(urls) {
  document.querySelector('input[name="fmt"][value="mp3"]').checked = true;
  const added = addPendingLinks(urls);
  formError.textContent = '';
  formNote.textContent = `Added ${added} links for MP3 audio` +
    (urls.length > added ? `; skipped ${urls.length - added} duplicates.` : '.');
  $('downloadSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('useCollection').addEventListener('click', async () => {
  if (!currentCollection) return;
  $('useCollection').disabled = true;
  try {
    const data = await collectionRequest(`/collections/${currentCollection}/links`);
    useCollectedLinks(data.urls);
  } catch (error) {
    collectorErrors.textContent = error.message;
  } finally {
    $('useCollection').disabled = false;
  }
});

const linksFile = $('linksFile'), importLinks = $('importLinks');
importLinks.addEventListener('click', () => linksFile.click());
linksFile.addEventListener('change', async () => {
  const file = linksFile.files[0];
  if (!file) return;
  importLinks.disabled = true;
  const body = new FormData();
  body.append('file', file);
  try {
    const data = await collectionRequest('/links/import', { method: 'POST', body });
    useCollectedLinks(data.urls);
    if (data.skipped) formNote.textContent += ` Skipped ${data.skipped} invalid or duplicate file rows.`;
  } catch (error) {
    formError.textContent = error.message;
  } finally {
    importLinks.disabled = false;
    linksFile.value = '';
  }
});

(async () => {
  try {
    const id = await refreshCollectionHistory();
    if (id) await loadCollection(id, true);
  } catch (error) {
    collectorErrors.textContent = 'Could not load saved collections: ' + error.message;
  }
})();
