"""Account-specific gates and isolated subprocess browser adapters."""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .account_profile import saved_profile, self_profile, avatar_url

PLATFORMS = {'douyin':'抖音','kuaishou':'快手','xiaohongshu':'小红书','wechat_channels':'视频号','bilibili':'B站','youtube':'YouTube'}
VALID_TTL = 12 * 3600


class AccountService:
    def __init__(self, root, store, runtime):
        self.root, self.store, self.runtime = Path(root), store, Path(runtime)
        self.locks = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(2)
        self.account_mutexes={}

    def mutex(self,key):
        with self.lock:
            return self.account_mutexes.setdefault(key,threading.Lock())

    def folder(self, account):
        path = self.root / 'data/accounts' / account['id']
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, account):
        folder = self.folder(account) / 'accounts'
        folder.mkdir(parents=True,exist_ok=True)
        return folder / (account['platform'] + '.json')

    def env(self, account):
        env = os.environ.copy()
        adapter = self.root / 'vendor/publisher'
        env.update(PUBLISHER_STATE=str(self.folder(account)), DREAMSTUDIO_ACCOUNT_FILE=str(self.state_path(account)),
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                   PYTHONPATH=os.pathsep.join([str(self.root/'vendor'),str(adapter),str(adapter/'vendor')]),
                   PLAYWRIGHT_BROWSERS_PATH=str(self.runtime/'browsers'))
        chromes = list((self.runtime/'browsers').glob('chromium-*/chrome-win64/chrome.exe'))
        if chromes:
            env['DREAM_ROOM_CHROME'] = str(chromes[0])
        env['PATH'] = str(self.runtime/'bin') + os.pathsep + env.get('PATH','')
        return env

    def public(self, account):
        result = {k:v for k,v in account.items() if k not in ('credential','storage_state','cookie','creator_storage_state','proxy','sec_uid')}
        try:
            profile=saved_profile(json.loads(self.state_path(account).read_text(encoding='utf-8')),account['platform'])
            if not result.get('avatar_url'):result['avatar_url']=profile.get('avatar_url','')
        except (OSError,ValueError,TypeError):pass
        result['avatar_url']=avatar_url(result.get('avatar_url'))
        if account.get('name') in ('小红书账号','抖音账号'):
            try:
                saved=json.loads(self.state_path(account).read_text(encoding='utf-8'))
                domains={'xiaohongshu':'https://creator.xiaohongshu.com','douyin':'https://www.douyin.com'}
                slots={'xiaohongshu':'USER_INFO_FOR_BIZ','douyin':'user_info'}
                for origin in saved.get('origins',[]):
                    if origin.get('origin')!=domains.get(account['platform']):continue
                    for item in origin.get('localStorage',[]):
                        if item.get('name')==slots.get(account['platform']):
                            profile=json.loads(item['value'])
                            name=profile.get('userName') or profile.get('nickname')
                            if name:result['name']=str(name)[:80]
            except (OSError,ValueError,TypeError,AttributeError):pass
        stamp = self.state_path(account).stat().st_mtime_ns if self.state_path(account).exists() else 0
        result['session_saved'] = bool(stamp)
        result['publish_state']=result.get('state','unchecked')
        result.setdefault('read_state','unchecked')
        result['stale'] = bool(account.get('checked_at') and (time.time()-account['checked_at']>=VALID_TTL or account.get('credential_stamp')!=stamp))
        if result['stale'] and result.get('state')=='valid':
            result['state'] = 'unchecked'
            result['publish_state']='unchecked'
        return result

    def issues(self, ids):
        problems=[]
        for key in dict.fromkeys(ids):
            a = self.store.get('accounts',key)
            if not a:
                problems.append({'id':key,'reason':'账号不存在'})
                continue
            a = self.public(a)
            if a.get('paused') or a.get('state')!='valid' or a.get('stale'):
                problems.append({'id':key,'platform':a['platform'],'alias':a.get('alias',''),
                                 'reason':'账号已停用' if a.get('paused') else a.get('message') or '请先检查账号'})
        return problems

    def identity(self, account):
        """Only authenticated self-profile data; never infer from other creators."""
        display=self.public(account)
        result={'name':display['name']} if display.get('name') else {}
        if display.get('avatar_url'):result['avatar_url']=display['avatar_url']
        if account['platform']=='douyin':
            try:
                import requests
                saved=json.loads(self.state_path(account).read_text(encoding='utf-8'))
                with requests.Session() as session:
                    for cookie in saved.get('cookies',[]):
                        if cookie.get('domain','').lstrip('.') in ('douyin.com','creator.douyin.com'):
                            session.cookies.set(cookie['name'],cookie['value'],domain=cookie['domain'],path=cookie.get('path','/'))
                    session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://creator.douyin.com/'})
                    payload=session.get('https://creator.douyin.com/web/api/media/user/info/',timeout=15).json()
                    user=payload.get('user') or {}
                    if payload.get('status_code')==0 and user.get('nickname'):
                        result.update(self_profile(user))
                        result['name']=str(user['nickname'])[:80]
                        identifier=user.get('unique_id') or user.get('short_id')
                        if identifier:result['identifier']=str(identifier)
            except Exception:pass
        return result

    def check(self, key, asynchronous=True):
        with self.lock:
            a = self.store.get('accounts',key)
            if not a:
                raise ValueError('账号不存在')
            if key in self.locks:
                return a
            self.locks[key]=True
        a['state']='checking'
        self.store.put('accounts',a)
        def run():
            try:
                with self.mutex(key),self.slots:
                    current=self.store.get('accounts',key)
                    path=self.state_path(current)
                    if not path.is_file():
                        result={'state':'missing','message':'尚未登录，请连接此账号。'}
                    elif current['platform']=='youtube':
                        result={'state':'unsupported','message':'YouTube 登录需使用专用下载校验；不能用 Cookie 文件存在作为在线证明。'}
                    else:
                        output=self.folder(current)/'health-result.json'
                        output.unlink(missing_ok=True)
                        try:
                            subprocess.run([sys.executable,str(self.root/'vendor/publisher/account_health_worker.py'),current['platform'],str(output)],
                                           env=self.env(current),cwd=self.root/'vendor',timeout=115,check=True,
                                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                            result=json.loads(output.read_text(encoding='utf-8'))
                        except subprocess.TimeoutExpired:
                            result={'state':'unavailable','message':'检测超时，尚未确认账号状态。'}
                        except (OSError,ValueError,subprocess.SubprocessError):
                            result={'state':'environment','message':'本机检测组件未完成，请查看运行检查。'}
                    current=self.store.get('accounts',key)
                    current.update(state=result.get('state','unavailable'),message=result.get('message','检测未确认'),
                                   checked_at=time.time(),credential_stamp=path.stat().st_mtime_ns if path.exists() else 0)
                    if result.get('name'):
                        current['name']=result['name']
                    if avatar_url(result.get('avatar_url')):current['avatar_url']=avatar_url(result['avatar_url'])
                    if current['state']=='valid':current.update(self.identity(current))
                    self.store.put('accounts',current)
                    self.store.event('账号检查',key+' · '+current['state'])
            except Exception:
                current=self.store.get('accounts',key)
                if current:
                    current.update(state='environment',message='检查未完成，请重试',checked_at=time.time())
                    self.store.put('accounts',current)
            finally:
                with self.lock:
                    self.locks.pop(key,None)
        if asynchronous:
            threading.Thread(target=run,daemon=True).start()
        else:
            run()
        return a

    def start_login(self,key):
        a=self.store.get('accounts',key)
        if not a:
            raise ValueError('账号不存在')
        if a['platform'] not in ('douyin','kuaishou','xiaohongshu','wechat_channels'):
            raise ValueError('此平台请使用账号页导入独立会话后检查。')
        jobs=self.folder(a)/'jobs'
        jobs.mkdir(exist_ok=True)
        for path in jobs.glob('*.json'):
            previous=json.loads(path.read_text(encoding='utf-8'))
            if previous.get('status') in ('queued','running') and previous.get('action')=='login':
                return previous
        job={'id':uuid.uuid4().hex,'platform':a['platform'],'action':'login','status':'queued','phase':'preparing','message':'正在准备二维码','created_at':time.time()}
        (jobs/(job['id']+'.json')).write_text(json.dumps(job,ensure_ascii=False),encoding='utf-8')
        def run():
            with self.mutex(key),self.slots:
                try:
                    job.update(status='running',message='正在打开登录页面')
                    (jobs/(job['id']+'.json')).write_text(json.dumps(job,ensure_ascii=False),encoding='utf-8')
                    subprocess.run([sys.executable,str(self.root/'vendor/publisher/runner.py'),job['id']],cwd=self.root/'vendor/publisher',env=self.env(a),timeout=420,
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                except subprocess.SubprocessError:
                    job.update(status='failed',message='登录未完成，请重新连接')
                    (jobs/(job['id']+'.json')).write_text(json.dumps(job,ensure_ascii=False),encoding='utf-8')
                self.check(key)
        threading.Thread(target=run,daemon=True).start()
        return job
