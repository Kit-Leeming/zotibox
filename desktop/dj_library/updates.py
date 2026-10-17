"""Explicit, local-folder Windows installer updates."""
import re
import subprocess
import sys
import hashlib
import requests
from pathlib import Path

VERSION = "0.12.0"
REPOSITORY = "Kit-Leeming/zotibox"


def github_update(current=VERSION,session=None):
    session=session or requests.Session()
    response=session.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest",headers={"Accept":"application/vnd.github+json"},timeout=20)
    if response.status_code==404:return None
    if response.status_code in {403,429}:raise ValueError("GitHub update checks are temporarily rate limited; try again later")
    response.raise_for_status();release=response.json()
    version=release["tag_name"].removeprefix("v")
    if release.get("draft") or release.get("prerelease") or version_key(version)<=version_key(current):return None
    name=f"ZotiBox-Setup-{version}.exe"
    asset=next((a for a in release["assets"] if a["name"]==name and a.get("state")=="uploaded"),None)
    if not asset:raise ValueError("The latest release has no Windows installer yet")
    digest=asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}",digest):raise ValueError("Release installer has no verified SHA-256 digest")
    url=asset["browser_download_url"]
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):raise ValueError("Unexpected update download location")
    return {"version":version,"url":url,"sha256":digest.split(":",1)[1].lower(),"name":name,"size":asset["size"]}


def download_update(update,folder,progress=lambda *a:None,cancel=None,session=None):
    session=session or requests.Session()
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if update["name"]!=f"ZotiBox-Setup-{update['version']}.exe":raise ValueError("Invalid installer name")
    target=folder/update["name"];partial=target.with_suffix(".partial")
    try:
        digest=hashlib.sha256();received=0
        with session.get(update["url"],stream=True,timeout=(15,45)) as response:
            response.raise_for_status()
            if not response.url.startswith("https://"):raise ValueError("Update redirected to an insecure location")
            with partial.open("wb") as stream:
                for chunk in response.iter_content(1024*1024):
                    if cancel and cancel.is_set():raise InterruptedError("Update download cancelled")
                    received+=len(chunk)
                    if received>update["size"]:raise ValueError("Installer size does not match release")
                    stream.write(chunk);digest.update(chunk)
                    progress(round(100*received/max(1,update["size"])),f"{received//1048576} / {update['size']//1048576} MB")
        if received!=update["size"] or digest.hexdigest()!=update["sha256"]:raise ValueError("Installer checksum failed; nothing installed")
        partial.replace(target);return target
    finally:partial.unlink(missing_ok=True)


def version_key(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    return tuple(map(int, version.split(".")))


def find_update(folder, current=VERSION):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError("Choose an available folder containing ZotiBox installers")
    candidates = []
    for path in folder.iterdir():
        match = re.fullmatch(r"ZotiBox-Setup-(\d+\.\d+\.\d+)\.exe", path.name, re.I)
        if match and path.is_file() and not path.is_symlink():
            version = match.group(1)
            if version_key(version) > version_key(current):
                candidates.append({"version": version, "path": path.resolve()})
    return max(candidates, key=lambda item: version_key(item["version"]), default=None)


def launch_installer(path):
    if not getattr(sys, "frozen", False):
        raise ValueError("Install the Windows build to apply an update")
    path = Path(path).resolve(strict=True)
    if not re.fullmatch(r"ZotiBox-Setup-\d+\.\d+\.\d+\.exe", path.name, re.I):
        raise ValueError("Select a ZotiBox release installer")
    # Same destination and AppId upgrade in place; installer owns file replacement.
    return subprocess.Popen([str(path), "/NORESTART", "/DIR=" + str(Path(sys.executable).parent)])
