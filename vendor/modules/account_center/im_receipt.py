"""Read-only protobuf receipt parser. Field numbers verified against Douyin's
creator SDK 7940.15e7b2e2.js on 2026-10-12. Never builds signed IM requests.
"""
import json


def fields(data):
    if not isinstance(data, bytes) or len(data) > 2_000_000:
        raise ValueError('Invalid protobuf')
    pos, result = 0, {}

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError('Truncated protobuf')
            b = data[pos]
            pos += 1
            value |= (b & 127) << shift
            if b < 128:
                return value
        raise ValueError('Invalid varint')

    while pos < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError('Invalid field')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else (8 if wire == 1 else 4)
            if pos+size > len(data):
                raise ValueError('Truncated field')
            value, pos = data[pos:pos+size], pos+size
        else:
            raise ValueError('Unsupported wire type')
        result[number] = value
    return result


def request_identity(data, uid, recipient, text):
    root = fields(data)
    body = fields(fields(root[8])[100])
    participants = body[1].decode().split(':')
    content = json.loads(body[4].decode())
    if root.get(1) != 100 or uid not in participants or recipient not in participants or content.get('text') != text:
        raise ValueError('Message identity mismatch')
    client = body.get(8)
    if not client:
        raise ValueError('Missing client id')
    return root.get(2), client


def receipt(data, identity):
    try:
        root = fields(data)
        if root.get(1) != 100 or root.get(2) != identity[0] or root.get(3, 0) != 0:
            return 'unknown'
        body = fields(fields(root[6])[100])
        if body.get(4) != identity[1]:
            return 'unknown'
        status = body.get(3, 0)
        if status == 0 and isinstance(body.get(1), int) and body[1] > 0 and body.get(5, 0) == 0:
            return 'sent'
        # Classify only explicit recipient restrictions. Generic moderation/rate errors are not privacy.
        reason = body.get(6, b'').decode('utf-8', errors='replace')
        if status in (1, 2, 3) and any(s in reason for s in ('对方设置了隐私', '对方仅接收', '对方不接收', '互相关注后才能发送', '对方已将你拉黑')):
            return 'restricted'
    except (ValueError, KeyError, TypeError, UnicodeError):
        pass
    return 'unknown'
