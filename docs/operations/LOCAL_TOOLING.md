# Optional local music-analysis tooling

CrateIQ keeps Python dependencies and external runtime binaries separate.
The tools below are optional: their absence can produce a `degraded` runtime
diagnostic, but does not prevent CrateIQ from starting or using its core
workflows. Settings shows each tool against the single advanced workflow it
would enable; a missing binary disables only that workflow. Install only the
tools needed for workflows you intentionally use. Do not vendor binaries in
this repository or commit local executable paths.

## Tool roles

| Tool | CrateIQ use | When it runs |
| --- | --- | --- |
| `keyfinder-cli` | Fallback musical-key/Camelot analysis | Only for a track without existing Mixed In Key (MIK) key data. |
| `aubio` | Preview-first, DB-only BPM analysis | Only after explicit confirmation for a track with null BPM. The in-app runner uses aubio only; it does not fall back to librosa. |
| `beet` | Beets metadata import/enrichment and the legacy organizer path | Only when the relevant import/organizer workflow is intentionally run; CrateIQ falls back to its Python organizer if `beet` is unavailable. |
| `rmlint` | Duplicate-detection workflow | Only when the user explicitly starts duplicate detection; no files are deleted automatically. |
| `ffprobe` + `ffmpeg` | Audio-quality/probing workflows | Only for explicit quality/probing or decode-support workflows. |

Mixed In Key remains authoritative for existing BPM, key, and cue data.
These tools fill only missing analysis; they must not be used to overwrite
trusted MIK values. MIK is a metadata input source, not a required executable:
Settings can explicitly preview compatible existing BPM/key tags and import
only missing values into CrateIQ's local index as `mik_compatible_tag` values.
That workflow never writes tags or audio and does not invoke an analyzer.
Cue-tag extraction is not implemented, so the coverage panel reports cue
support as unavailable. BPM and key preferences are default-off and never run
as part of library import.

The optional Analysis Jobs page (`/jobs`) shows a safe local-index candidate
preview for each tool-specific workflow. BPM is the exception: after preview
and explicit confirmation, it runs `aubio tempo <file>` for a small selected
limit and writes only valid 40–250 BPM plus lower-authority provenance to
CrateIQ's local index. It never writes audio/tags or replaces MIK/trusted BPM.
All other installed tools remain preview-only until their explicit DB-only
runner exists.

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

`keyfinder-cli` is not packaged in the default Linux Mint/Ubuntu Noble
repositories at the time this guide was verified. It is an optional fallback
for tracks that have no existing MIK key data; a missing executable must not
block CrateIQ startup or normal MIK-preserving workflows.

Use only the two upstream source repositories below. Do not download
third-party `.deb` files, prebuilt binaries from unverified mirrors, or vendor
the resulting executable in this repository:

- [Evan Purkhiser's keyfinder-cli](https://github.com/EvanPurkhiser/keyfinder-cli)
  is the CLI upstream. Its current `v1.2.0` release and CMake build recipe are
  suitable for a local source build.
- [Mixxx libkeyfinder](https://github.com/mixxxdj/libkeyfinder) is the
  maintained library upstream. Mixxx took over maintenance in 2020; use its
  current `2.2.8` tag rather than the original unmaintained library project.

The CLI is small and recently released, but it is not an Ubuntu/Mint-supported
package. Recommend it only as an opt-in local fallback, not as a required
CrateIQ dependency. Building the exact tagged sources below is preferred over
an unverified binary download.

#### Safe user-local source build

Review and run these commands yourself. They install only compiler and
development packages through the configured Ubuntu/Mint repositories, then
clone the two named upstream projects into a user-controlled directory. They
do not write to the CrateIQ checkout and do not analyze music files.

`ffmpeg` alone is not sufficient for a build: the CLI needs FFmpeg development
headers, while libkeyfinder needs FFTW3 headers. `BUILD_SHARED_LIBS=OFF` makes
libkeyfinder static inside the CLI so the installed executable does not depend
on a user-local `libkeyfinder.so` search path.

```bash
sudo apt update
sudo apt install build-essential cmake git pkg-config libfftw3-dev \
  libavcodec-dev libavformat-dev libavutil-dev libswresample-dev

export KEYFINDER_BUILD_ROOT="$HOME/src/keyfinder-build"
export KEYFINDER_PREFIX="$HOME/.local/opt/keyfinder-cli"
mkdir -p "$KEYFINDER_BUILD_ROOT" "$KEYFINDER_PREFIX"

git clone --depth 1 --branch 2.2.8 \
  https://github.com/mixxxdj/libkeyfinder.git \
  "$KEYFINDER_BUILD_ROOT/libkeyfinder-2.2.8"
cmake -S "$KEYFINDER_BUILD_ROOT/libkeyfinder-2.2.8" \
  -B "$KEYFINDER_BUILD_ROOT/libkeyfinder-2.2.8/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$KEYFINDER_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_TESTING=OFF
cmake --build "$KEYFINDER_BUILD_ROOT/libkeyfinder-2.2.8/build" --parallel
cmake --install "$KEYFINDER_BUILD_ROOT/libkeyfinder-2.2.8/build"

git clone --depth 1 --branch v1.2.0 \
  https://github.com/EvanPurkhiser/keyfinder-cli.git \
  "$KEYFINDER_BUILD_ROOT/keyfinder-cli-1.2.0"
cmake -S "$KEYFINDER_BUILD_ROOT/keyfinder-cli-1.2.0" \
  -B "$KEYFINDER_BUILD_ROOT/keyfinder-cli-1.2.0/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$KEYFINDER_PREFIX" \
  -DCMAKE_PREFIX_PATH="$KEYFINDER_PREFIX"
cmake --build "$KEYFINDER_BUILD_ROOT/keyfinder-cli-1.2.0/build" --parallel
cmake --install "$KEYFINDER_BUILD_ROOT/keyfinder-cli-1.2.0/build"
```

This places the executable at
`$HOME/.local/opt/keyfinder-cli/bin/keyfinder-cli`. The build and install
prefix are examples, not CrateIQ configuration; choose different directories
if preferred. Do not set either directory inside the repository or commit its
contents.

Verify the resulting program without passing it an audio file:

```bash
"$KEYFINDER_PREFIX/bin/keyfinder-cli" --help
test -x "$KEYFINDER_PREFIX/bin/keyfinder-cli"
```

Then either add its `bin` directory to your shell `PATH`, or use the explicit
override shown below. Keep `KEYFINDER_BIN` in a private local environment file
or export it in the shell that starts CrateIQ; do not commit the path.

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
curl -s http://127.0.0.1:8020/api/runtime/readiness | python3 -m json.tool
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

For the user-local build above:

```bash
export KEYFINDER_BIN="$HOME/.local/opt/keyfinder-cli/bin/keyfinder-cli"
"$KEYFINDER_BIN" --help
curl -s http://127.0.0.1:8020/api/runtime/readiness | python3 -m json.tool
```

Start or restart CrateIQ from that environment before checking readiness, so
the backend inherits the override. A passing optional-tool check confirms only
that the executable is discoverable; it does not run key analysis.

## Safety boundaries

- The tools are optional fallback tools, not startup requirements.
- A missing tool degrades readiness but never makes CrateIQ fail to start.
- Do not run analysis against a real music folder unless its root and the
  intended workflow are deliberately configured.
- Protect MIK BPM, key, and cue values: CrateIQ analysis is missing-data-only.
- Do not commit binaries, downloaded releases, or user-specific local paths.
