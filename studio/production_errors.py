"""Safe, actionable errors shared by manual and automatic production."""
class ProductionFailure(ValueError):
    def __init__(self, code, message, retryable=False, details=None):
        super().__init__(message)
        self.code=code
        self.retryable=retryable
        self.details=details or {}


def public_failure(exc):
    if isinstance(exc,ProductionFailure):
        return dict(message=str(exc),error_code=exc.code,retryable=exc.retryable,details=exc.details)
    if isinstance(exc,OSError):
        import errno
        reason=('文件不存在或已移动' if isinstance(exc,FileNotFoundError) else
                '文件被占用或没有读写权限' if isinstance(exc,PermissionError) else
                '磁盘空间不足' if exc.errno==errno.ENOSPC else f'本地文件读写失败（错误码 {exc.errno}）')
        return dict(message=reason+'；已保存内容保留，请排除原因后继续。',error_code='local_io',retryable=False,details={'errno':exc.errno})
    if isinstance(exc,ValueError):
        return dict(message=str(exc)[:240],error_code='input',retryable=False,details={})
    return dict(message='本机处理异常，已完成内容保留。可在任务内继续；再次失败时请查看故障记录。',
                error_code='environment',retryable=True,details={})


def ai_failure(exc):
    code=getattr(exc,'status_code',None)
    name=type(exc).__name__.lower()
    if code in (401,403):
        return ProductionFailure('ai_auth','AI 服务拒绝访问，请在“设置 → 制作服务”修改 API 密钥或模型权限。')
    if code==402:
        return ProductionFailure('ai_quota','AI 服务额度不足，请在服务商补充额度后继续；已完成翻译保留。')
    if code==429:
        return ProductionFailure('ai_rate_limit','AI 服务限流或额度不足，已暂停请求；请检查服务商额度，稍后从断点继续。')
    if code in (400,404,422):
        return ProductionFailure('ai_config','AI 接口或模型不兼容，请在“设置 → 制作服务”核对接口地址、模式和模型。')
    if 'timeout' in name or 'connection' in name or isinstance(code,int) and code>=500:
        return ProductionFailure('ai_network','AI 服务连接暂时失败；已完成翻译保留，可从断点继续。',True)
    return ProductionFailure('ai_response','AI 未返回可用内容，已完成内容保留，可从断点继续。',True)
