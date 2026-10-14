# Windows build and releases

Use Python 3.12 x64. Create `.venv`, install `requirements-build.txt` and this project.
Place FFmpeg/FFprobe and their shared DLLs in `tools/ffmpeg`, with `LICENSE.txt`.
Install Inno Setup in `tools/InnoSetup`, or make `ISCC.exe` available on PATH.
Run `scripts/build.ps1`. Tests must pass before packaging.

The installer and portable EXEs are under `dist`. Binaries are staged separately
so rebuilding does not erase an adjacent ZotiBox Database.

For a release, update the app version in `updates.py`, `pyproject.toml`,
installer/build scripts and source archive name in `build/app.spec`.
Commit/push the source, build and validate, then run:

```
.venv\Scripts\python.exe scripts\publish_release.py
```

The script uses the account already authorised through Git Credential Manager,
checks it is Kit-Leeming, and publishes the installer, SHA-256 file and app source.
It does not upload library databases, login files or music. GitHub's asset digest
is used by the app to verify updates. Release publishing is an explicit command.
