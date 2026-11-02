// Sidebar: switches between the TikTok / Facebook / YouTube / Hugging Face views.
// Every section is always in the page (app.js and collector.js keep working);
// a view only shows the sections whose data-views lists it.
const VIEWS = {
  tiktok: {
    title: 'TikTok downloader',
    desc: 'Collect links from creators, or paste TikTok video and photo-post links, then save them as MP3 or MP4.',
    placeholder: 'https://www.tiktok.com/@creator/video/...',
    hint: 'Photo posts (/photo/...) can only be saved as MP3 audio.',
  },
  facebook: {
    title: 'Facebook downloader',
    desc: 'Paste public Facebook video or Reel links, then save them as MP3 audio or MP4 video.',
    placeholder: 'https://www.facebook.com/watch?v=...  or a /reel/ link',
    hint: 'Only public posts work. Private, friends-only, or login-required videos cannot be downloaded.',
  },
  youtube: {
    title: 'YouTube downloader',
    desc: 'Paste videos, Shorts, playlists, or a channel’s /shorts page, then save them as MP3 or MP4.',
    placeholder: 'https://www.youtube.com/watch?v=...  a Short, a playlist, or a channel’s /shorts page',
    hint: 'If YouTube asks you to sign in, upload cookies in the YouTube sign-in box below.',
  },
  huggingface: {
    title: 'Push to Hugging Face',
    desc: 'Upload your finished MP3s to a Hugging Face dataset.',
  },
};

function showView(name) {
  if (!VIEWS[name]) name = 'tiktok';
  const view = VIEWS[name];
  document.querySelectorAll('[data-views]').forEach(el => {
    el.hidden = !el.dataset.views.split(' ').includes(name);
  });
  document.querySelectorAll('.nav-item').forEach(a => {
    const active = a.dataset.view === name;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const icon = document.querySelector(`.nav-item[data-view="${name}"] .nav-icon`);
  const viewIcon = document.getElementById('viewIcon');
  viewIcon.className = `logo ${name}`;
  viewIcon.innerHTML = icon ? icon.innerHTML : '';
  document.getElementById('viewTitle').textContent = view.title;
  document.getElementById('viewDesc').textContent = view.desc;
  if (view.placeholder) document.getElementById('urlInput').placeholder = view.placeholder;
  document.getElementById('platformHint').textContent = view.hint || '';
  document.title = `${view.title} · Video & Audio Downloader`;
}

window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
showView(location.hash.slice(1));
