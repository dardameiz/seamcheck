// Fixture. Mirrors the two shapes a real config uses: a quoted key and a bare one.
// Paths are relative to THIS FILE's directory, because that is what __dirname means: the
// directory holding the config. They used to be written relative to the repository root,
// which is the same directory only for a config that sits AT the repository root - a real
// one does, this fixture does not - and that read as correct for exactly as long as the
// entries were resolved against the process's working directory instead of __dirname.
export default {
  build: {
    rollupOptions: {
      input: {
        'base': resolve(__dirname, 'fixture_entry.js'),
        main: resolve(__dirname, 'fixture_module.js'),
      },
    },
  },
};
