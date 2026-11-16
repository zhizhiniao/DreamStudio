"""Own services, PID identity and inter-process start lock."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
import psutil

ROOT=Path(__file__).resolve().parents[1]
CONFIG=json.loads((ROOT/'runtime.json').read_text(encoding='utf-8'))
PYTHON=CONFIG['python']
STATE=ROOT/'data/instance.json'
STOP=ROOT/'data/stop.request'
URL='http://127.0.0.1:8020'


def read_state():
    try:
        state=json.loads(STATE.read_text(encoding='utf-8'));p=psutil.Process(state['pid'])
        if abs(p.create_time()-state['created'])>.1 or str(Path(__file__).resolve()) not in p.cmdline():return None
        return state
    except (OSError,ValueError,KeyError,psutil.Error):return None


def healthy():
    try:
        with urllib.request.urlopen(URL+'/health',timeout=2) as response:
            return json.load(response).get('application')=='dreamstudio'
    except OSError:return False


def stop_process(pid):
    try:
        p=psutil.Process(pid);processes=p.children(recursive=True)+[p]
        for child in reversed(processes):
            try:child.terminate()
            except psutil.Error:pass
        _,alive=psutil.wait_procs(processes,timeout=8)
        for child in alive:
            try:child.kill()
            except psutil.Error:pass
    except psutil.NoSuchProcess:pass


def daemon():
    import msvcrt
    guard=(ROOT/'data/instance.lock').open('a+b');guard.seek(0)
    if not guard.read(1):guard.write(b'0');guard.flush()
    guard.seek(0)
    try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:return
    children=[]
    try:
        for port in (8020,8021):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:raise RuntimeError(f'Port {port} already in use')
        env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONPATH=str(ROOT),
                    PLAYWRIGHT_BROWSERS_PATH=str(Path(CONFIG['runtime'])/'browsers'))
        env['PATH']=str(Path(CONFIG['runtime'])/'bin')+os.pathsep+env.get('PATH','')
        bundled_hub=(ROOT/'vendor/creatorhub/app').is_dir()
        hub_command=[CONFIG['hub_python'],str(ROOT/'tools/hub_server.py')] if bundled_hub else [PYTHON,str(ROOT/'tools/external_hub_stub.py')]
        external_file=ROOT/'data/external-hub.json'
        external_url=json.loads(external_file.read_text(encoding='utf-8')).get('url') if external_file.exists() else None
        commands=[] if external_url else [('hub',hub_command)]
        commands.append(('studio',[PYTHON,'-m','studio.server']))
        for name,command in commands:
            with (ROOT/'logs'/(name+'.log')).open('ab',buffering=0) as log:
                children.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW))
        STATE.write_text(json.dumps({'pid':os.getpid(),'created':psutil.Process().create_time(),'children':[p.pid for p in children]}),encoding='utf-8')
        while all(p.poll() is None for p in children) and not STOP.exists():time.sleep(.5)
    finally:
        for child in reversed(children):stop_process(child.pid)
        STATE.unlink(missing_ok=True);STOP.unlink(missing_ok=True);guard.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','status','daemon']);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    if args.action=='daemon':daemon();return
    if args.action=='status':print(json.dumps({'running':bool(read_state()),'healthy':healthy(),'url':URL}));return
    if args.action=='stop':
        if read_state():
            STOP.write_text('stop',encoding='utf-8')
            for _ in range(50):
                if not read_state():break
                time.sleep(.5)
            if read_state():raise RuntimeError('停止超时，请查看 logs')
        print('DreamStudio stopped');return
    if not read_state():
        with (ROOT/'logs/launcher.log').open('ab',buffering=0) as out:
            subprocess.Popen([PYTHON,str(Path(__file__).resolve()),'daemon'],cwd=ROOT,stdout=out,stderr=out,creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(100):
        if healthy():break
        time.sleep(.5)
    if not healthy():raise RuntimeError('启动失败，请查看 logs')
    print('DreamStudio ready: '+URL)
    if not args.no_browser:webbrowser.open(URL)


if __name__=='__main__':main()
