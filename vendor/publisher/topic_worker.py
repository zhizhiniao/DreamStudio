"""Bounded read of the creator's visible topic cards; never submits content."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.account_center.automation_browser import BrowserTransport
from modules.topic_library import CATEGORIES, SOURCE


def run(category):
    state = Path(os.environ['PUBLISHER_STATE']) / 'accounts/douyin.json'
    stamp = state.stat().st_mtime_ns
    with BrowserTransport(state) as browser:
        page = browser.page
        page.goto(SOURCE, wait_until='domcontentloaded')
        page.get_by_role('heading', name='灵感中心').wait_for(timeout=25000)
        for label in ['我知道了']:
            button = page.get_by_role('button', name=label, exact=True)
            if button.count() and button.is_visible():
                button.click()
        page.locator('[class*="video-base-title-text-"]').first.wait_for(timeout=20000)
        previous = page.locator('[class*="video-base-title-text-"]').all_text_contents()
        page.get_by_text(CATEGORIES[category], exact=True).click()
        if category != 'all' and previous:
            page.wait_for_function("old => JSON.stringify([...document.querySelectorAll('[class*=\"video-base-title-text-\"]')].map(e=>e.textContent)) !== JSON.stringify(old)", arg=previous)
        if 'each-kind-light-' not in (page.get_by_text(CATEGORIES[category], exact=True).get_attribute('class') or ''):
            raise ValueError('Category not selected')
        page.locator('[class*="video-base-title-text-"]').first.wait_for(timeout=20000)
        # Wait for DOM cards to settle after the category selection, not just initial navigation.
        page.locator('[class*="video-base-content-"] [class*="each-info-"]').first.wait_for(timeout=20000)
        body = page.locator('body').inner_text()
        if '24小时' not in body or '播放最高' not in body:
            raise ValueError('Unexpected period or sort')
        rows = page.locator('[class*="video-base-content-"]').evaluate_all("""elements => elements.map(e => ({
            name: e.querySelector('[class*="video-base-title-text-"]')?.textContent?.trim(),
            plays: [...e.querySelectorAll('[class*="each-info-"]')].find(x => x.querySelector('img')?.src.includes('/play.'))?.textContent?.trim()
        }))""")
        rows = [dict(name=r['name'], plays=r.get('plays') or '', rank=i+1) for i,r in enumerate(rows) if r.get('name') and len(r['name']) <= 20]
        if not rows or state.stat().st_mtime_ns != stamp:
            raise ValueError('No verified rows or login changed')
        return dict(rows=rows[:10], captured_at=datetime.now(timezone.utc).isoformat(), source=SOURCE,
                    platform='抖音', category=CATEGORIES[category], period='24小时', sort='播放最高',
                    scope='页面首屏前 10 条', stamp=stamp)

if __name__ == '__main__':
    try:
        print(json.dumps(run(sys.argv[1]), ensure_ascii=True))
    except Exception:
        print(json.dumps({'error': '话题读取未完成，请确认抖音登录有效，或稍后重试；可先粘贴导入话题。'}, ensure_ascii=True))
        sys.exit(1)
