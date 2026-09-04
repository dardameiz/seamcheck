"""A handler that delegates, which is the shape every real handler has.

`current_season` touches nothing itself. Its helper reads a Redis key and queries the
table, and joining the two is what makes a page map reach past the server band.
"""
from django.core.cache import cache

from .models import Season


def current_season(request):
    return _load_season(request.user)


def _load_season(user):
    cache_key = "fixture:season:current"
    found = cache.get(cache_key)
    if found is None:
        found = Season.objects.filter(is_active=True, owner=user).first()
        cache.set(cache_key, found, 30)
    return found


def _broken_query():
    # `naem` is not a field: Django raises FieldError the moment this line runs.
    return Season.objects.values("naem")
