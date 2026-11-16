"""No external write is retried after an ambiguous receipt."""
import time


class StopTask(Exception):
    pass


class TransportError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def execute(store, job, transport):
    jid, payload = job['id'], job['payload']
    rule, comment = payload['rule'], payload['comment']
    stage = job['state']
    if not store.allowed(job):
        return
    if stage in ('queued', 'retry_wait') and not store.allowed(job, dm=True):
        store.update(jid, 'retry_wait', '达到本规则 24 小时发送上限，稍后继续。', due=time.time()+600)
        return
    try:
        transport.verify_account(job['account_id'])
        if stage in ('queued', 'retry_wait'):
            store.update(jid, 'preparing', '正在核对接收人及私信入口。')
            transport.prepare_dm(comment, rule['dm_text'])

            def before_dm():
                if not store.allowed(job, dm=True):
                    raise StopTask()
                transport.verify_account(job['account_id'])
                store.update(jid, 'dm_sending', '私信已进入提交阶段，等待明确回执。', sent_at=time.time())

            receipt = transport.send_dm(rule['dm_text'], before_dm)
            store.record_outcome(jid, dm=receipt if receipt in ('sent','restricted') else 'unknown')
            if receipt == 'sent':
                stage = 'dm_sent'
                store.update(jid, stage, '私信发送回执已确认。')
            elif receipt == 'restricted':
                stage = 'dm_restricted'
                store.update(jid, stage, '平台明确提示接收人私信权限受限。')
            else:
                store.update(jid, 'needs_check', '私信结果未确认；不会重发，也不会回评已发送。')
                store.pause(job['account_id'])
                return
        reply = rule['success_reply'] if stage == 'dm_sent' else rule['restricted_reply']
        if not reply:
            store.update(jid, 'completed', '接收人受限，未配置公开引导文案。')
            return
        if not store.allowed(job):
            return
        transport.prepare_reply(comment, rule)

        def before_reply():
            if not store.allowed(job):
                raise StopTask()
            transport.verify_account(job['account_id'])
            store.update(jid, 'reply_sending', '正在提交原评论回复。')

        result = transport.send_reply(comment, reply, before_reply)
        store.record_outcome(jid, reply='sent' if result else 'unknown')
        store.update(jid, 'completed' if result else 'needs_check',
                     '私信结果与原评论回评均已确认。' if result else '回评结果未确认；私信不会重复发送。')
        if not result:
            store.pause(job['account_id'])
    except StopTask:
        # A confirmed DM remains resumable as reply-only; unsent work is cancelled.
        if stage not in ('dm_sent', 'dm_restricted'):
            store.update(jid, 'cancelled', '任务提交前规则已暂停。')
    except Exception as exc:
        # Consult the durable checkpoint, not just a local variable.
        with store.db() as db:
            current = db.execute('SELECT state FROM jobs WHERE id=?', (jid,)).fetchone()['state']
        if current == 'cancelled':
            return
        if current in ('dm_sending', 'reply_sending'):
            store.update(jid, 'needs_check', '提交后未获得可信回执，请核对平台记录；不会自动重发。')
            store.pause(job['account_id'])
            return
        message = exc.message if isinstance(exc, TransportError) else '平台页面暂时无法完成核对。'
        if isinstance(exc, TransportError) and exc.code in ('auth', 'identity', 'duplicate', 'challenge'):
            store.update(jid, 'needs_check', message)
            store.pause(job['account_id'])
        else:
            attempts = job['attempts'] + 1
            state = stage if stage in ('dm_sent', 'dm_restricted') else 'retry_wait'
            store.update(jid, 'needs_check' if attempts >= 3 else state, message,
                         attempts=attempts, due=time.time()+120 * (2 ** min(attempts, 4)))
