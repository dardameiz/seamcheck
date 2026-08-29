export function readCounter() {
  return document.getElementById('shared-counter').textContent;
}

export function toggleGift(on) {
  document.getElementById('gift-btn').classList.toggle('active', on);
}

export function dynamicLookup(name) {
  return document.getElementById(`row-${name}`);
}
