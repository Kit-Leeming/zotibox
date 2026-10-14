"""Publish this built release to the explicitly configured public repository."""
import hashlib
import os
import subprocess
import tomllib
from pathlib import Path
import requests

root=Path(__file__).resolve().parent.parent
version=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
repository='Kit-Leeming/zotibox'
installer=root/'dist'/f'ZotiBox-Setup-{version}.exe'
source=root/'build'/'generated'/f'ZotiBox-Source-{version}.zip'
if not installer.is_file() or not source.is_file():raise SystemExit('Build and validate the release first')
check=installer.with_suffix('.exe.sha256')
check.write_text(hashlib.sha256(installer.read_bytes()).hexdigest()+'  '+installer.name+'\n',encoding='utf-8')
credential=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never'),timeout=20)
fields=dict(line.split('=',1) for line in credential.stdout.splitlines() if '=' in line)
if not fields.get('password'):raise SystemExit('Sign in to GitHub through Git Credential Manager first')
session=requests.Session();session.headers.update({'Authorization':'Bearer '+fields['password'],'Accept':'application/vnd.github+json'})
profile=session.get('https://api.github.com/user',timeout=20);profile.raise_for_status()
if profile.json()['login'].casefold()!='kit-leeming':raise SystemExit('The signed-in GitHub account is not Kit-Leeming')
existing=session.get(f'https://api.github.com/repos/{repository}/releases/tags/v{version}',timeout=20)
if existing.status_code!=404:raise SystemExit('Release already exists or cannot be checked; published assets will not be replaced')
body=f'Windows release {version}. Install over the current app folder; keep your existing database. See README for setup.\n\nRekordbox writes require opt-in, a preview and verified backups.'
response=session.post(f'https://api.github.com/repos/{repository}/releases',json={'tag_name':'v'+version,'target_commitish':'main',
    'name':'ZotiBox '+version,'body':body,'draft':True,'prerelease':False},timeout=30)
response.raise_for_status();release=response.json();upload=release['upload_url'].split('{',1)[0]
for path in [installer,check,source]:
    with path.open('rb') as stream:
        response=session.post(upload,params={'name':path.name},data=stream,headers={'Content-Type':'application/octet-stream'},timeout=(20,300))
    response.raise_for_status()
    if path==installer:
        digest=response.json().get('digest')
        if digest!='sha256:'+hashlib.sha256(installer.read_bytes()).hexdigest():raise SystemExit('Uploaded installer digest does not match; release left as draft')
    print('Uploaded',path.name)
response=session.patch(f'https://api.github.com/repos/{repository}/releases/{release["id"]}',json={'draft':False},timeout=30)
response.raise_for_status();print(response.json()['html_url'])
