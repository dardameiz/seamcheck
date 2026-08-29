export function updateCounterB(value) {
  document.getElementById('shared-counter').innerHTML = value;
  document.querySelector('.stat-value').style.color = 'red';
}

export function updateViaBinding(value) {
  const el = document.getElementById('bound-counter');
  el.textContent = value;
}

export class BoundManager {
  init() {
    this.box = document.getElementById('bound-counter');
  }
  render(value) {
    this.box.innerHTML = value;
  }
}
