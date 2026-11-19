// Tabs: switches between the YouTube / Facebook / Pinterest / All sites /
// Hugging Face views.
// Every section is always in the page (app.js keeps working); a view only
// shows the sections whose data-views lists it. Sections without data-views
// (the queue) are always shown.
const VIEWS = {
  youtube: {
    title: 'YouTube downloader',
    desc: 'Paste videos, Shorts, playlists or whole channels, then save them as MP3 audio or MP4 video.',
    placeholder: 'Paste a video, Short, playlist or channel link',
    hint: 'Playlists and channels are expanded into their videos. If YouTube asks you to sign in, upload cookies below.',
  },
  facebook: {
    title: 'Facebook downloader',
    desc: 'Paste public Facebook video or Reel links, then save them as MP3 audio or MP4 video.',
    placeholder: 'Paste a facebook.com/watch or /reel/ link',
    hint: 'Only public posts work. Private, friends-only or login-required videos cannot be downloaded.',
  },
  pinterest: {
    title: 'Pinterest downloader',
    desc: 'Paste public Pinterest video Pin links, then save them as MP3 audio or MP4 video.',
    placeholder: 'Paste a pinterest.com/pin/ link',
    hint: 'Video Pins are supported. Image Pins and private or login-required Pins cannot be downloaded as video.',
  },
  other: {
    title: 'All sites downloader',
    desc: 'TikTok, Instagram, X (Twitter), Vimeo, Reddit, SoundCloud, Dailymotion, Bilibili and 1,800+ more sites. Save as MP3 audio or MP4 video.',
    placeholder: 'Paste a video link from any site',
    hint: 'Any site yt-dlp supports works. Pinterest boards and TikTok profiles are expanded into their videos. Only public posts can be downloaded.',
  },
  huggingface: {
    title: 'Push to Hugging Face',
    desc: 'Upload the MP3s you have finished downloading to a Hugging Face dataset.',
  },
};

function showView(name) {
  if (!VIEWS[name]) name = 'youtube';
  const view = VIEWS[name];
  document.body.dataset.view = name;
  document.querySelectorAll('[data-views]').forEach(el => {
    el.hidden = !el.dataset.views.split(' ').includes(name);
  });
  document.querySelectorAll('.tab').forEach(a => {
    const active = a.dataset.view === name;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const icon = document.querySelector(`.tab[data-view="${name}"] .tab-icon`);
  const viewIcon = document.getElementById('viewIcon');
  viewIcon.className = `hero-icon ${icon ? icon.classList[1] : ''}`;
  viewIcon.innerHTML = icon ? icon.innerHTML : '';
  const svg = viewIcon.querySelector('svg');
  if (svg) { svg.setAttribute('width', '26'); svg.setAttribute('height', '26'); }
  document.getElementById('viewTitle').textContent = view.title;
  document.getElementById('viewDesc').textContent = view.desc;
  if (view.placeholder) document.getElementById('urlInput').placeholder = view.placeholder;
  document.getElementById('platformHint').textContent = view.hint || '';
  document.title = `${view.title} · Video & Audio Downloader`;
}

window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
showView(location.hash.slice(1));

// Light / dark theme. With no saved choice the page follows the system.
document.getElementById('themeToggle').addEventListener('click', () => {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('theme', root.dataset.theme); } catch {}
});
