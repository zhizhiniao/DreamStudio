"""Keep TLS verification enabled with an ASCII CA path for Windows curl."""
import os
import shutil
from pathlib import Path
import certifi

if __name__=='__main__':
    original=Path(certifi.where())
    target=Path(os.environ.get('LOCALAPPDATA','C:/Temp'))/'DreamStudio/certificates/cacert.pem'
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists() or target.read_bytes()!=original.read_bytes():shutil.copy2(original,target)
    os.environ['CURL_CA_BUNDLE']=str(target)
    os.environ['SSL_CERT_FILE']=str(target)
    certifi.where=lambda:str(target)
    import yt_dlp
    yt_dlp.main()
