# AlienHand Godot GDExtension

This folder is the external C++ extension path for AlienHand.

It is different from the Godot engine module in
`C:\Project\Codex_Projects\LLMOS-Compiler\modules\alienhand_agent`:

- The engine module is compiled into the Godot fork.
- This GDExtension builds a DLL/shared library that Godot loads from a project.

Godot is not the C++ compiler or IDE in this flow. It loads the compiled library
through a `.gdextension` manifest. The compiler is still MSVC/Clang/GCC, and the
build system is usually SCons with the `godot-cpp` bindings.

## Why `godot-cpp` Is Not Just a Universal Redistributable

`godot-cpp` is the official C++ binding layer for GDExtension. It is normally
built for a specific target platform, architecture, precision mode, and Godot API
version. With a custom Godot fork, the safest route is to generate
`extension_api.json` from that fork and build `godot-cpp` against it.

The compiled output you redistribute is your extension DLL plus its
`.gdextension` file. You do not need to ship the `godot-cpp` source repository
with the final game project, but you do need it or an equivalent prebuilt static
library at build time.

## Layout

- `SConstruct`: SCons build script for the extension.
- `src/`: C++ extension source.
- `project/`: tiny Godot project/addon layout used for loading the extension.
- `tools/setup_godot_cpp.ps1`: optional helper to clone `godot-cpp`.

## Setup

From this folder:

```powershell
python -m pip install scons
.\tools\setup_godot_cpp.ps1
```

Build your Godot fork, then generate an API file from that exact executable:

```powershell
C:\Project\Codex_Projects\LLMOS-Compiler\bin\godot.windows.editor.x86_64.exe --dump-extension-api
```

Then build the extension:

```powershell
scons platform=windows custom_api_file=extension_api.json
```

For release:

```powershell
scons platform=windows target=template_release custom_api_file=extension_api.json
```

The DLL should appear under `project/addons/alienhand/bin/`.

## First Class

The starter class is `AlienHandTelemetry`. It mirrors the engine module's
JSONL recording idea and gives Godot projects a project-local telemetry bridge.
