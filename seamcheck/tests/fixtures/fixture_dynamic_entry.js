const loaders = {
  widget: () => import('./fixture_dynamic_target.js'),
};

export function loadWidget() {
  return loaders.widget();
}

export function loadUnresolvable(name) {
  // Not a bare string literal - must NOT be followed.
  return import(`./buttons/${name}.js`);
}
