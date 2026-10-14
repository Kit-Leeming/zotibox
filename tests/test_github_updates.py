import hashlib
import pytest
from dj_library.updates import github_update,download_update


class Response:
    def __init__(self,data=None,status=200,body=b'installer'):
        self.data=data;self.status_code=status;self.body=body;self.url='https://release-assets.githubusercontent.com/file'
    def json(self):return self.data
    def raise_for_status(self):
        if self.status_code>=400:raise ValueError('HTTP failed')
    def iter_content(self,size):yield self.body
    def __enter__(self):return self
    def __exit__(self,*_):pass


class Session:
    def __init__(self,response):self.response=response
    def get(self,*args,**kwargs):return self.response


def release():
    return {'tag_name':'v0.5.0','draft':False,'prerelease':False,'assets':[{'name':'ZotiBox-Setup-0.5.0.exe','state':'uploaded',
        'digest':'sha256:'+hashlib.sha256(b'installer').hexdigest(),'size':9,
        'browser_download_url':'https://github.com/Kit-Leeming/zotibox/releases/download/v0.5.0/ZotiBox-Setup-0.5.0.exe'}]}


def test_release_and_checksum_validation(tmp_path):
    update=github_update(session=Session(Response(release())))
    path=download_update(update,tmp_path,session=Session(Response()))
    assert path.read_bytes()==b'installer'
    with pytest.raises(ValueError,match='checksum'):
        download_update(update,tmp_path,session=Session(Response(body=b'bad bytes')))
    assert path.read_bytes()==b'installer'
    assert not list(tmp_path.glob('*.partial'))


def test_unpublished_prerelease_and_host_validation():
    assert github_update(session=Session(Response(status=404))) is None
    data=release();data['prerelease']=True
    assert github_update(session=Session(Response(data))) is None
    data=release();data['assets'][0]['browser_download_url']='https://example.com/installer.exe'
    with pytest.raises(ValueError,match='location'):github_update(session=Session(Response(data)))
    data=release();data['assets'][0]['digest']=None
    with pytest.raises(ValueError,match='digest'):github_update(session=Session(Response(data)))
