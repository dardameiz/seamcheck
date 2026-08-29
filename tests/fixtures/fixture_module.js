export function loadThing() {
  fetch('/api/get-thing/').then((r) => r.json());
}

export function callGhost() {
  fetch('/api/does-not-exist/');
}

export function callDynamic(id) {
  fetch(`/api/items/${id}/`);
}

export const arrowCaller = () => {
  fetch('/api/from-arrow/');
};

export class Reporter {
  report() {
    navigator.sendBeacon('/api/log/', '{}');
  }
}

export function loadNested() {
  fetch('/sub/nested/');
}
