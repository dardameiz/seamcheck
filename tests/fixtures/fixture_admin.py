from django.contrib import admin


class ThingAdmin(admin.ModelAdmin):
    actions = ["do_thing", "not_a_method_here"]

    def do_thing(self, request, queryset):
        pass
