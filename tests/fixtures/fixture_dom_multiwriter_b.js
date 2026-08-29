export function updateCounterB(value) {
  document.getElementById('shared-counter').innerHTML = value;
  document.querySelector('.stat-value').style.color = 'red';
}
