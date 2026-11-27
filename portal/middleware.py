import hashlib
import time

from django.core.cache import cache
from django.http import HttpResponse


class LimitAccountPosts:
    """Bound repeated password and email-link submissions in the single web worker."""

    paths = {"/account/login/", "/account/reset/", "/manage/login/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path in self.paths:
            remote = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "")
            subject = (request.POST.get("username") or request.POST.get("email") or "").strip().casefold()
            window = int(time.time()) // 600
            for value, limit in ((f"ip:{remote}", 60), (f"subject:{subject}", 12)):
                if value == "subject:":
                    continue
                digest = hashlib.sha256(value.encode()).hexdigest()
                key = f"account:{request.path}:{window}:{digest}"
                cache.add(key, 0, 600)
                if cache.incr(key) > limit:
                    return HttpResponse("Too many attempts. Try again in a few minutes.", status=429)
        return self.get_response(request)
