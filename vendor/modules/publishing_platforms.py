PLATFORM_LABELS = {'bilibili': 'B站', 'douyin': '抖音', 'wechat_channels': '微信视频号', 'xiaohongshu': '小红书', 'kuaishou': '快手'}
EXTERNAL_PLATFORMS = ('douyin', 'wechat_channels', 'xiaohongshu', 'kuaishou')
CREATOR_URLS = {
    'kuaishou': 'https://cp.kuaishou.com/',
    'douyin': 'https://creator.douyin.com',
    'wechat_channels': 'https://channels.weixin.qq.com',
    'xiaohongshu': 'https://creator.xiaohongshu.com',
}
MANAGE_URLS = {
    'kuaishou': 'https://cp.kuaishou.com/article/manage/video',
    'douyin': 'https://creator.douyin.com/creator-micro/content/manage',
    'wechat_channels': 'https://channels.weixin.qq.com/platform/post/list',
    'xiaohongshu': 'https://creator.xiaohongshu.com/new/note-manager',
}
LOGIN_HELP = {
    'kuaishou': '使用快手 App 扫码，并在手机上确认。',
    'douyin': '使用抖音 App 扫码。',
    'wechat_channels': '使用视频号管理员微信扫码。',
    'xiaohongshu': '使用小红书 App 扫码，并在手机上确认。',
}
PLATFORM_LIMITS = {
    'kuaishou': {'title_limit': 30, 'description_limit': 800},
    'bilibili': {'title_limit': 80, 'description_limit': 2000},
    'douyin': {'title_limit': 30, 'description_limit': 800},
    'wechat_channels': {'title_limit': 16, 'description_limit': 800},
    'xiaohongshu': {'title_limit': 20, 'description_limit': 800},
}

def platform_label(target):
    return PLATFORM_LABELS.get(target, '已停用平台')

def metadata_limits(target):
    return dict(PLATFORM_LIMITS[target])


def topic_limit(target):
    return 3 if target == 'kuaishou' else 6


def normalize_wechat_title(title):
    import re
    title = re.sub(r'(?<=\d)\.(?=\d)', '点', str(title or ''))
    return ''.join(c for c in title if c.isalnum() or c in '《》“”:+?%°， ')[:16].strip()
