"""Account-bound, short-lived receipts for starting the Snapchat bot."""
import base64
import hashlib
import hmac
import re
import time


def signature(secret, value):
    if len(secret) < 32:
        raise ValueError('BOT_ACTIVATION_SECRET must contain at least 32 characters')
    digest = hmac.new(secret.encode(), ('snap-start-v1:' + value).encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip('=')


def issue(secret, user_id):
    value = f'{user_id}_{int(time.time())}'
    return f'snap_{value}_{signature(secret, value)}'


def verify(secret, token, user_id):
    match = re.fullmatch(r'snap_(\d{1,16})_(\d{10})_([A-Za-z0-9_-]{22})', token)
    if not match or int(match[1]) != user_id:
        return False
    age = time.time() - int(match[2])
    if not 0 <= age <= 600:
        return False
    try:
        expected = signature(secret, f'{match[1]}_{match[2]}')
    except ValueError:
        return False
    return hmac.compare_digest(expected, match[3])
