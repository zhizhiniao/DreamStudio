"""Extract only authenticated self-profile fields, never cookies or feed authors."""
import json
from urllib.parse import urlsplit


def avatar_url(value):
    if isinstance(value,dict):value=value.get('url_list') or value.get('url') or ''
    if isinstance(value,list):value=next((x for x in value if isinstance(x,str)),'')
    if not isinstance(value,str):return ''
    if value.startswith('//'):value='https:'+value
    parsed=urlsplit(value)
    return value if parsed.scheme=='https' and parsed.hostname and not parsed.username and len(value)<3000 else ''


def self_profile(profile):
    if not isinstance(profile,dict):return {}
    if isinstance(profile.get('user'),dict):profile=profile['user']
    result={}
    name=profile.get('userName') or profile.get('nickname') or profile.get('uname') or profile.get('nickName')
    if isinstance(name,str) and name.strip():result['name']=name.strip()[:80]
    for key in ('userAvatar','avatarUrl','avatar_url','avatar_thumb','avatar_medium','avatar','headUrl','headImgUrl','face'):
        url=avatar_url(profile.get(key))
        if url:result['avatar_url']=url;break
    return result


def saved_profile(state,platform):
    if not isinstance(state,dict):return {}
    slots={'xiaohongshu':('USER_INFO_FOR_BIZ',),'douyin':('user_info','user_info_passport')}
    domains={'xiaohongshu':('creator.xiaohongshu.com',),'douyin':('www.douyin.com','creator.douyin.com')}
    result={}
    for origin in state.get('origins',[]):
        if urlsplit(origin.get('origin','')).hostname not in domains.get(platform,()):continue
        for item in origin.get('localStorage',[]):
            if item.get('name') not in slots.get(platform,()):continue
            try:
                for key,value in self_profile(json.loads(item['value'])).items():result.setdefault(key,value)
            except (ValueError,TypeError,KeyError):pass
    return result
