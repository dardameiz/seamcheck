// Fixture. Mirrors the two shapes a real config uses: a quoted key and a bare one.
// Paths are written as a real vite.config.js writes them - relative to the repo root,
// because that is where __dirname points and the extractor reads the literal.
export default {
  build: {
    rollupOptions: {
      input: {
        'base': resolve(__dirname, 'seamcheck/tests/fixtures/fixture_entry.js'),
        main: resolve(__dirname, 'seamcheck/tests/fixtures/fixture_module.js'),
      },
    },
  },
};
