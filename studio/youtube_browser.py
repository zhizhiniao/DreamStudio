"""Dedicated local browser session. Never reads the user's default browser profile."""
import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import youtube
from vendor.modules.cookiecloud import build_youtube_netscape_cookies
from vendor.modules.youtube_auth import save_cookies


class BrowserSession:
    def __init__(self, data, runtime):
        self.data, self.runtime = Path(data), Path(runtime)
        self.process = None
        self.lock = threading.Lock()
        self.status_path = self.data/'youtube-browser-status.json'

    def status(self):
        value = youtube.read_json(self.status_path)
        running = self.process is not None and self.process.poll() is None
        if not running and value.get('state') in ('opening', 'waiting', 'refreshing'):
            value.update(state='closed', message='登录窗口已关闭，可重新打开。')
        return dict(**value, running=running,
                    profile_saved=(self.data/'youtube-browser'/'Default').is_dir())

    def start(self, visible=True):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            youtube.write_json(self.status_path, dict(state='opening' if visible else 'refreshing',
                message='正在打开 YouTube 登录窗口…' if visible else '正在刷新登录会话…', at=time.time()))
            env=os.environ.copy()
            env['PLAYWRIGHT_BROWSERS_PATH']=str(self.runtime/'browsers')
            command=[sys.executable, '-m', 'studio.youtube_browser', str(self.data)]
            if not visible: command.append('--refresh')
            self.process=subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return self.status()

    def refresh(self, force=False):
        if self.status()['running']: return self.status()
        if not self.status()['profile_saved']: return self.status()
        if not force and time.time()-self.status().get('at',0)<300: return self.status()
        self.start(visible=False)
        process=self.process
        try: process.wait(timeout=50)
        except subprocess.TimeoutExpired:
            import psutil
            try:
                parent=psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    try: child.kill()
                    except psutil.Error: pass
                parent.kill()
            except psutil.Error: pass
            youtube.write_json(self.status_path, dict(state='network',at=time.time(),
                message='自动刷新超时，已保留原会话。请检查网络或重新打开登录窗口。'))
        return self.status()


def run(data, refresh=False):
    from patchright.sync_api import sync_playwright
    data=Path(data)
    status=data/'youtube-browser-status.json'
    def report(state,message,**extra):
        youtube.write_json(status,dict(state=state,message=message,at=time.time(),**extra))
    cfg=youtube.read_json(data/'youtube-settings.json')
    options=dict(user_data_dir=str(data/'youtube-browser'), headless=refresh,
                 viewport=None, timeout=25000)
    if cfg.get('proxy_url'): options['proxy']={'server':cfg['proxy_url']}
    # Prefer a locally installed stable browser, with bundled Chromium as fallback.
    for candidate in (Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',
                      Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'):
        if candidate.is_file(): options['executable_path']=str(candidate); break
    saved=False
    try:
        with sync_playwright() as p:
            # The portable runtime ships full Chromium, not chromium-headless-shell.
            options.setdefault('executable_path',p.chromium.executable_path)
            context=p.chromium.launch_persistent_context(**options)
            try:
                page=context.pages[0] if context.pages else context.new_page()
                try: page.goto('https://www.youtube.com/' if refresh else 'https://www.youtube.com/signin',wait_until='domcontentloaded',timeout=25000)
                except Exception:
                    report('network','YouTube 页面暂时无法打开，请检查网络；原会话已保留。')
                    if refresh: return
                else: report('waiting','请在打开的窗口登录 YouTube；完成后会自动保存，无需导出文件。')
                deadline=time.monotonic()+8 if refresh else float('inf')
                last=None
                while context.pages and time.monotonic()<deadline:
                    try:
                        cookies=context.cookies()
                        content,_=build_youtube_netscape_cookies({'cookie_data':{'youtube.com':cookies}})
                        # Validates expiry and login fields before atomic replacement.
                        if content!=last:
                            count=save_cookies(content,data/'youtube-cookies.txt')
                            last=content
                            saved=True
                            report('saved','登录会话已自动保存，可以关闭窗口并返回造梦室。',count=count)
                        if refresh and saved: break
                    except ValueError:
                        report('login_required','当前窗口尚未取得有效 YouTube 登录字段，原有会话已保留。请确认已进入 YouTube 首页。',cookie_count=len(cookies))
                    except Exception as exc:
                        from vendor.modules.cookiecloud import CookieCloudError
                        if not isinstance(exc,CookieCloudError): raise
                        report('login_required','尚未读取到 YouTube 会话，原有会话已保留。',cookie_count=len(cookies))
                    context.pages[0].wait_for_timeout(2000)
                if not saved: report('login_required','尚未取得有效登录会话，请重新打开窗口完成登录；原会话已保留。')
            finally: context.close()
    except Exception:
        if not saved:
            report('browser_error','登录窗口未完成，请重试。若页面无法连接，请设置网络代理；原会话已保留。')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('data');parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args();run(args.data,args.refresh)
