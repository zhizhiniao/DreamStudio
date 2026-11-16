"""Douyin's own UI supplies signing and IM certificates; no security bypasses."""
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .automation_engine import TransportError
from .im_receipt import request_identity, receipt

COMMENT_URL = 'https://creator.douyin.com/creator-micro/interactive/comment'
SEARCH_PATH = '/web/api/third_party/aweme/api/comment/read/aweme/v1/web/comment/list/select/'


class BrowserTransport:
    def __init__(self, state_path):
        self.state_path = Path(state_path)

    def __enter__(self):
        from patchright.sync_api import sync_playwright
        self.stamp = self.state_path.stat().st_mtime_ns
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.launch(headless=True, executable_path=os.environ.get('DREAM_ROOM_CHROME') or None)
            self.context = self.browser.new_context(storage_state=str(self.state_path), viewport={'width': 1440, 'height': 1000})
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(30000)
            self.uid = None
            return self
        except BaseException:
            self.pw.stop()
            raise

    def __exit__(self, *args):
        self.context.close()
        self.browser.close()
        self.pw.stop()

    def verify_account(self, uid):
        if self.state_path.stat().st_mtime_ns != self.stamp:
            raise TransportError('auth', '登录状态已更新，规则已暂停，请核对账号后重新启用。')
        response = self.context.request.get('https://creator.douyin.com/web/api/media/user/info/', timeout=15000)
        try:
            data = response.json()
            actual = str((data.get('user') or {}).get('uid') or '')
        except Exception:
            actual = ''
        if actual != uid:
            raise TransportError('auth', '平台登录失效或账号已切换，规则已暂停。')
        self.uid = uid

    def select_work(self, rule):
        self.page.goto(COMMENT_URL, wait_until='domcontentloaded')
        self.page.get_by_role('button', name='选择作品', exact=True).click()
        # The list UI uses the work's description. Duplicate titles are ambiguous and must stop.
        title = rule['work_title'].strip()
        dialog = self.page.get_by_role('dialog')
        choice = dialog.get_by_text(title, exact=False)
        for _ in range(10):
            if choice.count():
                break
            dialog.hover()
            self.page.mouse.wheel(0, 1400)
            self.page.wait_for_timeout(500)
        choice.click()

    def search(self, rule, keyword):
        self.select_work(rule)
        box = self.page.get_by_placeholder('搜索评论关键词')
        with self.page.expect_response(lambda r: SEARCH_PATH in r.url and
                                       parse_qs(urlsplit(r.url).query).get('aweme_id') == [rule['work_id']], timeout=20000) as wait:
            box.fill(keyword)
            box.press('Enter')
        response = wait.value.json()
        if response.get('status_code') != 0 or not isinstance(response.get('comments'), list):
            raise TransportError('read', '平台未返回有效评论，请检查登录状态。')
        rows = list(response['comments'])
        for _ in range(9):
            if not response.get('has_more'):
                break
            with self.page.expect_response(lambda r: SEARCH_PATH in r.url and
                                           parse_qs(urlsplit(r.url).query).get('aweme_id') == [rule['work_id']], timeout=12000) as wait:
                self.page.mouse.wheel(0, 1800)
            response = wait.value.json()
            if response.get('status_code') != 0:
                raise TransportError('read', '评论分页读取失败，本轮未发送。')
            rows.extend(response.get('comments') or [])
        if response.get('has_more'):
            raise TransportError('read', '关键词结果超过 200 条，请使用更精确的关键词。')
        return rows

    def collect(self, rule):
        self.verify_account(rule['account_id'])
        found = {}
        for keyword in rule['keywords']:
            for row in self.search(rule, keyword):
                user = row.get('user') or {}
                cid, uid, sec = str(row.get('cid') or ''), str(user.get('uid') or ''), user.get('sec_uid') or ''
                if str(row.get('aweme_id')) != rule['work_id'] or not cid.isdigit() or not uid.isdigit() or not re.fullmatch(r'MS4w[\w-]+', sec):
                    raise TransportError('identity', '评论缺少可信的接收人标识，本轮未发送。')
                if str(row.get('reply_id') or '0') != '0':
                    continue
                found[cid] = {'id': cid, 'recipient': uid, 'sec_uid': sec, 'author': user.get('nickname') or '未知昵称',
                              'text': row.get('text') or '', 'created_at': row.get('create_time') or 0,
                              'is_author': uid == self.uid, 'work_id': rule['work_id']}
        return list(found.values())

    def prepare_dm(self, comment, text):
        self.target = comment
        self.page.goto('https://creator.douyin.com/creator-micro/data/following/chat?user='+quote(comment['sec_uid']), wait_until='domcontentloaded')
        self.page.locator('[contenteditable="true"][class*="chat-input-"]').wait_for()
        # Independently verify sec_uid maps to the exact commenter, never a nickname search.
        r = self.context.request.get('https://creator.douyin.com/aweme/v1/creator/relation/transform/uid', params={'sec_uid': comment['sec_uid']}, timeout=15000).json()
        if r.get('status_code') != 0 or str(r.get('uid')) != comment['recipient']:
            raise TransportError('identity', '私信接收人身份核对失败，规则已暂停。')
        if self.page.locator('[class*="is-me-"] pre').filter(has_text=text).count():
            raise TransportError('duplicate', '该会话已有相同文案，请核对历史发送记录；不会重复发送。')

    def send_dm(self, text, before_submit):
        identity = None
        submitted = False
        outcome = 'unknown'
        blocked = None

        def guard(route):
            nonlocal identity, submitted, blocked
            try:
                if submitted:
                    route.abort()
                    return
                identity = request_identity(route.request.post_data_buffer, self.uid, self.target['recipient'], text)
                before_submit()
                submitted = True
                route.continue_()
            except Exception as exc:
                blocked = exc if isinstance(exc, TransportError) else TransportError('identity', '私信请求未通过接收人与文案校验，已阻止提交。')
                route.abort()

        def write_guard(route):
            r = route.request
            if r.method in ('GET', 'HEAD', 'OPTIONS'):
                route.continue_()
            elif urlsplit(r.url).hostname == 'imapi.douyin.com' and urlsplit(r.url).path.rstrip('/') == '/v1/message/send':
                guard(route)
            else:
                # Fail closed if a future SDK changes its submission endpoint.
                route.abort()
        self.page.route('**/*', write_guard)
        try:
            self.page.locator('[contenteditable="true"][class*="chat-input-"]').fill(text)
            with self.page.expect_response(lambda r: urlsplit(r.url).hostname == 'imapi.douyin.com' and '/v1/message/send' in r.url, timeout=25000) as wait:
                self.page.get_by_role('button', name='发送', exact=True).click()
            if identity:
                outcome = receipt(wait.value.body(), identity)
        except Exception:
            if blocked:
                raise blocked
            raise
        finally:
            # Destroy this SDK instance before removing its guard, including all pending retries.
            self.page.close()
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(30000)
        return outcome

    def prepare_reply(self, comment, rule):
        self.reply_rule = rule
        rows = self.search(rule, comment['text'])
        matched = [r for r in rows if str(r.get('cid')) == comment['id'] and str((r.get('user') or {}).get('uid')) == comment['recipient']]
        if len(matched) != 1:
            raise TransportError('read', '原评论已不可见或身份发生变化，没有发送回评。')
        # Reject ambiguous visible rows; the outgoing request is also checked below.
        row = self.page.locator('[class*="container-sXKyMs"]').filter(
            has=self.page.get_by_text(comment['text'], exact=True)).filter(
            has=self.page.get_by_text(comment['author'], exact=True))
        if row.count() != 1:
            raise TransportError('read', '原评论在页面中无法唯一定位，没有发送回评。')
        row.get_by_text('回复', exact=True).click()
        self.reply_editor = self.page.locator('[contenteditable="true"][placeholder^="回复 "]:visible')
        self.reply_editor.wait_for()

    def send_reply(self, comment, text, before_submit):
        sent = False

        def guard(route):
            nonlocal sent
            try:
                raw = route.request.post_data or ''
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = {k: v[0] for k, v in parse_qs(raw).items()}
                if sent or str(data.get('reply_id') or data.get('comment_Id') or '') != comment['id'] or str(data.get('aweme_id') or data.get('item_id') or '') != comment['work_id'] or data.get('text') != text:
                    route.abort()
                    return
                before_submit()
                sent = True
                route.continue_()
            except Exception:
                route.abort()

        pattern = '**/*'
        def post_guard(route):
            if route.request.method == 'POST' and urlsplit(route.request.url).hostname == 'creator.douyin.com' and '/comment/' in route.request.url and ('publish' in route.request.url or '/reply/' in route.request.url):
                guard(route)
            elif route.request.method in ('GET', 'HEAD', 'OPTIONS'):
                route.continue_()
            else:
                route.abort()
        self.page.route(pattern, post_guard)
        try:
            self.reply_editor.fill(text)
            with self.page.expect_response(lambda r: r.request.method == 'POST' and '/comment/' in r.url and ('publish' in r.url or '/reply/' in r.url), timeout=20000) as wait:
                self.page.get_by_role('button', name='发送', exact=True).and_(self.page.locator(':enabled:visible')).click()
            data = wait.value.json()
            item = data.get('comment') or data.get('comment_info') or {}
            confirmed = sent and data.get('status_code') == 0 and bool(item.get('cid') or item.get('comment_id'))
        finally:
            self.page.close()
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(30000)
        if not confirmed:
            return False
        reply_id = str(item.get('cid') or item.get('comment_id'))
        # Fresh platform read; a local optimistic bubble is not proof of delivery.
        roots = self.search(self.reply_rule, comment['text'])
        parent = next((r for r in roots if str(r.get('cid')) == comment['id']), {})
        return any(str(r.get('cid')) == reply_id and r.get('text') == text and
                   str((r.get('user') or {}).get('uid')) == self.uid for r in parent.get('reply_comment') or [])
