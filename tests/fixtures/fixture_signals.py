from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="fixtures.SomeModel")
def on_some_model_saved(sender, instance, **kwargs):
    pass


@receiver(post_save, sender="fixtures.SomeModel")
async def on_some_model_saved_async(sender, instance, **kwargs):
    pass
