"""Per-publication evidence for Douyin video transfers. Never persist signed URLs or bodies."""
import asyncio
import json
import re
from collections import deque
from urllib.parse import parse_qs, urlsplit


class UploadRejected(RuntimeError):
    pass


def transfer_operation(url, method):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    path = parsed.path.lower()
    query = {key.lower(): value[0] for key, value in parse_qs(parsed.query, keep_blank_values=True).items()}
    action = query.get('action', '')
    if re.fullmatch(r'[A-Za-z]{1,50}', action) and re.search('upload|vod|video|commit', action, re.I):
        return host, action
    if any(part in path for part in ('upload', 'tos/', 'vod/', '/commit', 'media/user/auth')) or (
        method in ('PUT','POST','OPTIONS') and any(word in host for word in ('vod','upload','tos-','-tos'))):
        phase = query.get('phase', '').lower()
        if phase in ('check','init','initiate','transfer','upload','finish','complete','part','query'):
            return host, phase
        if 'partnumber' in query:
            return host, 'part'
        if 'uploadid' in query:
            return host, 'complete' if method == 'POST' else 'check'
        if 'uploads' in query:
            return host, 'init'
        return host, 'upload'
    return None


def response_codes(body):
    if not isinstance(body, dict):
        return []
    codes = []
    for key, value in body.items():
        k = key.lower().replace('_','')
        if isinstance(value, dict) and k in ('responsemetadata','error','data','result','response','status'):
            codes.extend(response_codes(value))
        elif k in ('code','errorcode','statuscode') and isinstance(value, (int,str)):
            value = str(value)
            if value not in ('0','200','OK','Success','success') and re.fullmatch(r'-?\d{1,12}|[A-Za-z][A-Za-z0-9_.-]{0,63}', value):
                codes.append(value)
    return list(dict.fromkeys(codes))


def message_hints(value):
    # Only fixed diagnostic words are retained, even when an SDK message embeds credentials.
    return list(dict.fromkeys(match.group().lower() for match in re.finditer(
        r'\b(?:progress has not been updated for over a minute|success|ok|not found|not exist|file not exist|no such upload|access denied|timeout|timed out|abort(?:ed)?|network error|net error|failed to fetch|crc(?:32)? mismatch|invalid crc(?:32)?|connection reset|signature expired|invalid signature|internal error|not supported|invalid argument|file changed|file not readable|NotReadableError|NetworkError)\b|ERR_[A-Z0-9_]{2,60}',
        str(value), re.I)))


def sdk_message(value):
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return ' '.join(filter(None, (sdk_message(value.get(key)) for key in
            ('name','code','errc','errorCode','status','statusText','errText','message','msg','error'))))[:240]
    if not isinstance(value, str):
        return ''
    if re.fullmatch(r'-?\d{1,12}',value):
        return value
    return ' '.join(message_hints(value))[:240]


def unpack(value):
    if isinstance(value,str) and value.lstrip().startswith(('{','[')):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


class UploadTrace:
    def __init__(self, directory, job_id):
        self.job_id = job_id
        self.path = directory / (job_id + '-upload-diagnostics.json')
        self.events = deque(maxlen=60)
        self.errors = deque(maxlen=24)
        self.pending = set()
        self.page_error = ''

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = {'job_id':self.job_id, 'page_error':self.page_error, 'errors':list(self.errors), 'events':list(self.events)}
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(info, ensure_ascii=False, indent=2), 'utf-8')
        temp.replace(self.path)

    def record(self, request, **fields):
        operation = transfer_operation(request.url, request.method)
        if not operation:
            return
        host, stage = operation
        item = dict(host=host, operation=stage, method=request.method, **fields)
        if not self.events or self.events[-1] != item:
            self.events.append(item)
        if fields.get('codes') or fields.get('browser_error') or fields.get('http',0) >= 400:
            if item not in self.errors:
                self.errors.append(item)
                self.write()

    def request_failed(self, request):
        code = re.search(r'\bERR_[A-Z0-9_]+\b', request.failure or '')
        self.record(request, browser_error=code.group() if code else 'REQUEST_FAILED')

    def sdk_request(self, request):
        # The SDK reports the final stage separately from resumable probes and individual parts.
        path = urlsplit(request.url).path.lower()
        if request.method != 'POST' or not any(word in path for word in ('/collect','/report','/monitor','/log/','/app_log','/list')):
            return
        try:
            raw = request.post_data or ''
        except UnicodeError:
            return
        if len(raw) > 512000 or not re.search(r'errc|errorCode|error_code|upload_error|upload_fail', raw, re.I):
            return
        try:
            values = [json.loads(raw)]
        except ValueError:
            return
        while values:
            entry = values.pop()
            if isinstance(entry, list):
                values.extend(entry)
            elif isinstance(entry, dict):
                params = entry.get('params')
                if isinstance(params,str) and params.startswith('{'):
                    try:
                        values.append(json.loads(params))
                    except ValueError:
                        pass
                values.extend(v for v in entry.values() if isinstance(v, (dict,list)))
                code = entry.get('errc') or entry.get('errorCode') or entry.get('error_code')
                if not code or not re.fullmatch(r'-?[1-9]\d{0,11}', str(code)):
                    continue
                if not any(key in entry for key in ('upload_host','uploadId','errc','upload_error','upload_type','source_file_size')):
                    continue
                hints = message_hints(entry.get('errmsg','') or entry.get('err',''))
                sdk = {'operation':'SDK', 'codes':[str(code)], 'hints':hints}
                sdk['detail'] = ' '.join(filter(None, (sdk_message(unpack(entry.get(k))) for k in ('errmsg','err','ex','error_msg'))))
                result = unpack(entry.get('res'))
                if isinstance(result,dict):
                    status = result.get('status')
                    if str(status).lstrip('-').isdigit():
                        sdk['transfer_status'] = int(status)
                    sdk['response'] = sdk_message(unpack(result.get('body')))
                req = unpack(entry.get('req'))
                if isinstance(req,dict) and isinstance(req.get('url'),str):
                    target = transfer_operation(req['url'],'POST')
                    if target:
                        sdk['transfer_host'],sdk['transfer_operation'] = target
                stage = str(entry.get('stage',''))
                if re.fullmatch(r'\d{1,4}|preUpload|initUploadID|process|fileMerge|complete', stage):
                    sdk['stage'] = stage
                if sdk not in self.errors:
                    self.errors.append(sdk)
                    self.write()

    async def response(self, response):
        req = response.request
        if not transfer_operation(req.url, req.method):
            return
        self.record(req, http=response.status)
        content_type = response.headers.get('content-type','').lower()
        if 'json' not in content_type and not (response.request.method in ('GET','POST','PUT')
                and content_type.startswith('text/plain') and response.request.resource_type in ('xhr','fetch')):
            if response.status >= 400 and 'xml' in content_type:
                try:
                    message = await asyncio.wait_for(response.text(), 3)
                    code = re.search(r'<Code>([A-Za-z0-9_.-]{1,64})</Code>', message)
                    if code:
                        self.record(req, http=response.status, codes=[code[1]])
                except Exception:
                    pass
            return
        try:
            body = await asyncio.wait_for(response.json(), 3)
        except Exception:
            return
        codes = response_codes(body)
        if codes:
            hints = message_hints(body.get('message','') or body.get('msg','')) if isinstance(body,dict) else []
            # TOS returns 2000 for completed requests. Preserve it in the timeline only.
            if codes == ['2000'] and response.status == 200 and any(hint in ('success','ok') for hint in hints):
                self.record(req, http=response.status, result_code='2000', hints=hints)
            else:
                self.record(req, http=response.status, codes=codes, hints=hints)

    def observe(self, page):
        def received(response):
            task = asyncio.create_task(self.response(response))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        page.on('requestfailed', self.request_failed)
        page.on('request', self.sdk_request)
        page.on('request', lambda request: self.record(request, event='start'))
        page.on('response', received)

    async def flush(self):
        if self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)

    def summary(self):
        details = []
        items = sorted(reversed(self.errors), key=lambda item: (item['operation'] != 'SDK', item['operation'] == 'check'))
        for item in items:
            if item['operation'] == 'check' and item.get('codes') == ['4004']:
                continue
            codes = list(item.get('codes',[]))
            if item.get('browser_error'):
                codes.append(item['browser_error'])
            if item.get('http',0) >= 400:
                codes.append('HTTP ' + str(item['http']))
            for code in codes:
                if code not in details:
                    details.append(code)
        if not details:
            return '；未取得上传接口错误码'
        reason = '（视频分片超过一分钟无进度）' if any(
            'progress has not been updated for over a minute' in e.get('detail','') for e in self.errors) else ''
        return '；上传接口记录 ' + '、'.join(details[:5]) + reason

    async def save_failure(self, page):
        from kuaishou import save_page_evidence
        self.write()
        try:
            target = self.path.parent / (self.job_id + '-error.png')
            try:
                await save_page_evidence(page, target)
            except Exception:
                await page.screenshot(path=str(target), timeout=5000)
        finally:
            await self.flush()
            self.write()
