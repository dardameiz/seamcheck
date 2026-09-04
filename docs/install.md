# Install

```bash
pip install seamcheck
seamcheck map
```

That is the whole setup. It opens a map of your project and prints a link you can open on
your phone. **Have a look around before reading any further** — it explains itself better
than this page does.

Needs **Python 3.10 or newer**, and nothing else.

**If your project is Django**, put it in the project's own virtualenv:

```bash
source .venv/bin/activate
pip install seamcheck
```

A Django project is the one thing seamcheck reads by *importing* it, so it has to run where
that project's imports resolve. Every other backend is read from source, so anywhere works.

Tables, columns and queries are read from the source, so a checkout that cannot be imported
gets the same answer as a project that can. Nothing optional is needed for them.

For the agent server: `pip install 'seamcheck[mcp]'`.

<details>
<summary><b>If pip says <code>externally-managed-environment</code></b></summary>

That is [PEP 668](https://peps.python.org/pep-0668/). Homebrew's Python and most Linux
distro Pythons refuse to let pip install into them globally, on purpose — it is how you
break your OS. A virtualenv is the answer, and it is what you want here anyway:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```

Do not reach for `--break-system-packages`. It does what it says.
</details>

<details>
<summary><b>macOS</b></summary>

The `python3` that ships with macOS is **3.9**, which is too old — and it is why
`pip3 install seamcheck` can report *"could not find a version that satisfies the
requirement seamcheck (from versions: none)"*. That message means "nothing here matches
your Python", not "no such package".

```bash
brew install python@3.12
cd your-project
python3.12 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```
</details>

<details>
<summary><b>Debian / Ubuntu</b></summary>

```bash
sudo apt install python3-venv        # if `python3 -m venv` is missing
python3 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```
</details>

<details>
<summary><b>Windows</b></summary>

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install seamcheck
```
</details>

<details>
<summary><b>pipx and <code>uv tool</code> — read this before you use them</b></summary>

Both work and both give you `seamcheck` on your PATH everywhere:

```bash
pipx install seamcheck
uv tool install seamcheck        # uv fetches its own Python, so no Homebrew needed
```

**But a pipx or uv-tool copy is isolated from your project on purpose, so it cannot scan a
Django project** — importing your settings needs your project's own dependencies, and they
are not in there. Seamcheck will say so rather than showing you a traceback.

They are fine for everything else, since nothing there has to be imported: Express,
Fastify, NestJS, Next.js, Flask, FastAPI, and Supabase, Firebase or Redis projects.
</details>

<details>
<summary><b>Upgrading and getting an old version</b></summary>

pip caches the package index, so shortly after a release you can be handed the previous
one:

```bash
pip install --no-cache-dir --upgrade seamcheck
seamcheck --version
```
</details>
