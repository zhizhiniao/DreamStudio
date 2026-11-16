"""Private CreatorHub bridge: content reading, collection and monitoring only."""
import json
import re
from pathlib import Path
import requests


class Hub:
    def __init__(self,root):
        self.root=Path(root)
        self.url='http://127.0.0.1:8021'
        config_file=self.root/'data/external-hub.json'
        self.external={}
        if config_file.is_file():
            self.external=json.loads(config_file.read_text(encoding='utf-8'))
            self.url=str(self.external.get('url',self.url)).rstrip('/')
            if not re.match(r'^https?://',self.url):raise ValueError('外部采集服务地址无效')

    def request(self,method,path,payload=None,timeout=25):
        token=self.external.get('token') or (self.root/'data/hub-token').read_text(encoding='utf-8')
        try:
            result=requests.request(method,self.url+'/api/'+path,json=payload,
                                    headers={'X-DreamStudio-Token':token},timeout=timeout)
        except requests.RequestException:
            raise ValueError('采集服务暂未就绪，请检查 DreamStudio 运行状态。')
        if not result.ok:
            try:
                error=result.json().get('detail','采集服务未完成操作')
            except ValueError:
                error='采集服务未完成操作'
            raise ValueError(str(error)[:220])
        return result.json()

    def bind(self,account,state_file):
        if account['platform'] not in ('douyin','xiaohongshu','kuaishou','wechat_channels'):
            raise ValueError('该平台尚未接入关键词采集。')
        read_file=Path(state_file).parent.parent/'read'/Path(state_file).name
        if read_file.is_file():state_file=read_file
        return self.request('POST','studio-bind',{'id':account['id'],'platform':account['platform'],
                             'nickname':account['name'],'state_file':str(state_file),'sec_uid':account.get('sec_uid',''),
                             'uid':account.get('uid','')})['hub_id']
