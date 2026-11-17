# Bundled Tina 4.0.0

This directory contains the official Windows amd64 and Linux amd64 Tina
distributions, including their support files, documentation, examples, and
upstream ownership and usage notices. They come from
<https://projects.laas.fr/tina/download.php>.

```
4.0.0/windows-amd64/bin/tina.exe
4.0.0/linux-amd64/bin/tina
```

Run from the project root on either platform:

```sh
python -m petrinet examples/agents/example.txt --agentnet --timeout 60
```

Use `python3` on Linux if `python` is unavailable. No separate Tina installation
or PATH changes are required on Windows x86-64 and Linux x86-64. The Linux
package targets glibc 2.27 or later; it is not an ARM64 or musl/Alpine build.

Application analysis always uses Tina. It locates the executable in this order:

1. An executable supplied with `--tina PATH`.
2. The `TINA_BIN` environment variable.
3. The bundled executable matching the operating system and architecture.
4. `tina` on PATH.

The resolver restores user execute permission on the bundled Linux checker
when needed, including after a Git checkout created on Windows. Paths are
resolved relative to the project files, so the Python API also works from
other working directories. The checker only uses the `tina` executable;
the rest of each distribution is retained for its dependencies and companion
tools. Do not mix binaries or support files from the two platforms.

The original Windows download has been copied into the project. Archive source
URLs and SHA-256 hashes are recorded in `4.0.0/provenance.json`. The archives
themselves are not retained as duplicate copies. Upstream notices are in
`4.0.0/windows-amd64/README.txt` and `4.0.0/linux-amd64/README`; installation
instructions and the full manuals are in each platform directory.
