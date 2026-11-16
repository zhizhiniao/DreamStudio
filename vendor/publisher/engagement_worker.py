"""One bounded background sweep. Parent owns process lifetime and browser slot."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.account_center.automation_store import Store
from modules.account_center.automation_browser import BrowserTransport
from modules.account_center.automation_engine import execute, TransportError


def run():
    root = Path(os.environ['PUBLISHER_STATE'])
    store = Store(root / 'account-center/automation.sqlite3')
    rules = [r for r in store.rules() if r['enabled']]
    if not rules:
        store.heartbeat('idle', '后台待命；没有启用的规则。')
        return
    rule = None
    try:
        store.heartbeat('reading', '正在检查启用规则对应的视频评论。')
        rule = store.due_rule(claim=True)
        if rule:
            store.record_scan(rule['id'], 'running', '正在连接平台并读取评论。')
        with BrowserTransport(root / 'accounts/douyin.json') as browser:
            # Different saved-account scopes are never silently combined.
            account = rules[0]['account_id']
            browser.verify_account(account)
            # Discovery is scheduled independently from delivery, so a backlog never delays new-comment checks.
            if rule:
                if rule['account_id'] != account:
                    store.pause(rule['account_id'])
                else:
                    store.record_scan(rule['id'], 'running', '正在读取规则对应的视频评论。')
                    comments = browser.collect(rule)
                    added = sum(int(store.enqueue(rule, comment)) for comment in comments)
                    from modules.account_center.automation_store import matches
                    eligible = sum(c['created_at']>rule['since'] and not c.get('is_author') and matches(rule,c['text']) for c in comments)
                    store.record_scan(rule['id'], 'success', '本轮评论读取完成。', read=len(comments), matched=eligible, queued=added, deduplicated=eligible-added)
            job = store.next_job()
            if job:
                store.heartbeat('sending', '正在处理发送队列。')
                execute(store, job, browser)
            store.heartbeat('idle', '本轮完成；各视频每 10 分钟检查，发送队列独立处理。')
    except TransportError as exc:
        if rule:
            store.record_scan(rule['id'], 'error', exc.message)
        if exc.code in ('auth', 'identity', 'challenge'):
            for rule in rules:
                store.pause(rule['account_id'])
        store.heartbeat('error', exc.message)
    except Exception:
        if rule:
            store.record_scan(rule['id'], 'error', '本轮读取未完成，未确认的评论不会作为已处理。')
        store.heartbeat('error', '本轮平台读取未完成，稍后重试；发送结果不明的任务不会重发。')


if __name__ == '__main__':
    run()
