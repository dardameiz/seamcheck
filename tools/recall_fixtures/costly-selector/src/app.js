// PLANTED: a phantom poll. Fetches every 5s and writes an element that is in no template.
// This is the shape that cost the reference project ~10,000 requests/second.
setInterval(async () => {
  const response = await fetch('/api/stats');
  const data = await response.json();
  document.querySelector('.latency-readout').textContent = data.latency;
}, 5000);

// TRAP: dead too, but it costs one wasted call at load, not a request per user per 5s.
// Proximity alone cannot tell it from the one above - it must NOT be ranked as costly.
document.querySelector('.cheap-dead-thing');

// PLANTED: an observer watching for an element that is never rendered.
const watcher = new MutationObserver(() => {
  document.querySelector('.observed-ghost').classList.add('on');
});
watcher.observe(document.body, { childList: true });
