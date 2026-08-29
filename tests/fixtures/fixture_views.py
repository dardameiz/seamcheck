from django.http import JsonResponse


def get_thing(request):
    return JsonResponse({"value": 42})


def orphan_view(request):
    return JsonResponse({"ok": True})


def nested_thing(request):
    return JsonResponse({"nested": True})
