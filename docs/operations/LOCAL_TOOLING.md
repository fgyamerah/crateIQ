# Optional local music-analysis tooling

CrateIQ keeps Python dependencies and external runtime binaries separate.
The tools below are optional: their absence produces a `degraded` readiness
warning, but does not prevent CrateIQ from starting. Install only the tools
needed for the workflows you intentionally use. Do not vendor binaries in this
repository or commit local executable paths.

## Tool roles

| Tool | CrateIQ use | When it runs |
| --- | --- | --- |
| `keyfinder-cli` | Fallback musical-key/Camelot analysis | Only for a track without existing Mixed In Key (MIK) key data. |
| `aubio` | Fallback BPM analysis | Only for a track without existing BPM/MIK data. CrateIQ can also fall back to the existing Python `librosa` dependency when aubio is unavailable or fails. |
| `beet` | Beets metadata import/enrichment and the legacy organizer path | Only when the relevant import/organizer workflow is intentionally run; CrateIQ falls back to its Python organizer if `beet` is unavailable. |

Mixed In Key remains authoritative for existing BPM, key, and cue data.
These tools fill only missing analysis; they must not be used to overwrite
trusted MIK values.

## Linux Mint / Ubuntu installation

Run package-management commands yourself after reviewing them for your local
machine. This guide does not install tools automatically. The legacy
`setup.sh` bootstrap can install system/venv dependencies and configure Beets;
it is not required for this focused tooling setup and should not be run solely
to install one optional executable.

### aubio

On Linux Mint and Ubuntu, `aubio-tools` is normally the system package that
provides the `aubio` command (some releases also provide `aubiotrack`):

```bash
sudo apt update
sudo apt install aubio-tools
```

`aubio` is an external executable in this integration. Do not add the Python
`aubio` package to `requirements.txt` unless CrateIQ begins importing that
package in code.

### Beets

CrateIQ invokes Beets through the `beet` CLI; it does not import the `beets`
Python library. On Linux Mint/Ubuntu, prefer the distribution package for a
system-managed optional CLI:

```bash
sudo apt install beets
```

If you intentionally install Beets into an isolated virtual environment,
ensure that environment is active when running the workflow or point `BEET_BIN`
at its executable. This does not make Beets a mandatory CrateIQ Python
dependency, so it is deliberately absent from `requirements.txt` and
`requirements-dev.txt`.

### keyfinder-cli

`keyfinder-cli` may not be available in default Linux Mint/Ubuntu apt
repositories. Install it using a trusted distribution package when available,
or follow the upstream project's manual build/release-binary instructions.
The resulting executable can be placed on `PATH` or referenced with
`KEYFINDER_BIN`. Its location depends on whether it was installed from a
package, downloaded as a release binary, or built locally; do not assume or
commit a fixed path.

## Verify the local tools

After installation, verify the commands without scanning a music folder:

```bash
which keyfinder-cli
which aubio
which beet

keyfinder-cli --help
aubio --version
beet version
```

CrateIQ readiness performs only availability checks; it does not invoke these
commands or analyze audio. You can inspect its report with:

```bash
curl -s http://127.0.0.1:8020/api/runtime/readiness | python -m json.tool
```

## Optional executable overrides

Leave the following variables unset or blank to search `PATH`:

```dotenv
KEYFINDER_BIN=
AUBIO_BIN=
BEET_BIN=
```

For a non-standard installation, export only the relevant explicit path in
your shell or private local env file:

```bash
KEYFINDER_BIN=/path/to/keyfinder-cli
AUBIO_BIN=/path/to/aubio
BEET_BIN=/path/to/beet
```

The readiness endpoint respects these overrides and reports the unavailable
workflow if an override cannot be resolved. It does not return the configured
override value in a missing-tool warning. See [`.env.example`](../../.env.example)
for the repository template.

## Safety boundaries

- The tools are optional fallback tools, not startup requirements.
- A missing tool degrades readiness but never makes CrateIQ fail to start.
- Do not run analysis against a real music folder unless its root and the
  intended workflow are deliberately configured.
- Protect MIK BPM, key, and cue values: CrateIQ analysis is missing-data-only.
- Do not commit binaries, downloaded releases, or user-specific local paths.
