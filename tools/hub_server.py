"""CreatorHub runs with its own data directory and a private gateway."""
import json
import os
import secrets
import sys
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,os.environ.get('DREAMSTUDIO_CREATORHUB_SOURCE',str(ROOT/'vendor/creatorhub')))
os.environ['CREATORHUB_CONFIG_PATH']=str(ROOT/'data/hub-config.yaml')
from app.main import app
from app.db import get_session
from app.models import DouyinAccount
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import select

TOKEN=(ROOT/'data/hub-token').read_text(encoding='utf-8')

@app.middleware('http')
async def private_gateway(request:Request,call_next):
    if request.url.path=='/health':
        return await call_next(request)
    if not secrets.compare_digest(request.headers.get('X-DreamStudio-Token',''),TOKEN):
        return JSONResponse({'detail':'Private DreamStudio component'},status_code=403)
    # No automatic engagement, notification or publishing routes from the imported app.
    forbidden=('/api/comment-tasks','/api/comment-rules','/api/action-tasks','/api/publish','/api/channels','/api/im')
    if any(request.url.path.startswith(p) for p in forbidden):
        return JSONResponse({'detail':'此操作需要独立人工确认，当前桥接仅开放读取与采集'},status_code=403)
    if request.method not in ('GET','HEAD','OPTIONS'):
        allowed=(r'/api/studio-bind',r'/api/collections(?:/\d+/(?:cancel|retry))?',r'/api/share-download',
                 r'/api/monitors(?:/\d+/(?:toggle|run-now))?',r'/api/accounts/\d+/(?:works|follows|dm)/sync',
                 r'/api/account-works/\d+/comments/sync')
        if not any(re.fullmatch(pattern,request.url.path) for pattern in allowed):
            return JSONResponse({'detail':'DreamStudio 未开放此写入操作'},status_code=403)
    return await call_next(request)

class Bind(BaseModel):
    id:str
    platform:str
    nickname:str
    state_file:str
    sec_uid:str=''
    uid:str=''

@app.post('/api/studio-bind')
async def bind(body:Bind):
    file=Path(body.state_file).resolve()
    if not file.is_relative_to((ROOT/'data/accounts').resolve()) or not file.is_file():
        raise HTTPException(400,'独立会话文件无效')
    platform={'xiaohongshu':'xhs','wechat_channels':'shipinhao'}.get(body.platform,body.platform)
    with get_session() as session:
        profile=str(ROOT/'data/hub-profiles'/body.id)
        account=session.exec(select(DouyinAccount).where(DouyinAccount.profile_dir==profile)).first()
        if not account:
            account=DouyinAccount(platform=platform,sec_uid=body.sec_uid,uid=body.uid)
        account.nickname=body.nickname
        if body.sec_uid:account.sec_uid=body.sec_uid
        if body.uid:account.uid=body.uid
        account.storage_state=file.read_text(encoding='utf-8')
        account.creator_storage_state=account.storage_state
        account.status='active'
        account.profile_dir=profile
        session.add(account);session.commit();session.refresh(account)
        return {'hub_id':account.id}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8021,log_level='warning',access_log=False)
