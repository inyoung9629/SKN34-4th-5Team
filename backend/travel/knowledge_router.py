"""Optionally route only public place evidence to the team's shared PostgreSQL."""
from django.conf import settings

MODELS = {"placeknowledge", "placeknowledgesource", "placeknowledgeobservation", "placeenrichmentattempt"}


def knowledge_alias():
    return getattr(settings, "PLACE_KNOWLEDGE_DB_ALIAS", "default")


class PlaceKnowledgeRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "travel" and model._meta.model_name in MODELS:
            return knowledge_alias()

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        if all(o._meta.app_label == "travel" and o._meta.model_name in MODELS for o in (obj1, obj2)):
            return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        alias = knowledge_alias()
        if alias == "default":
            return None
        is_knowledge = app_label == "travel" and model_name in MODELS
        if db == alias:
            return is_knowledge
        if is_knowledge:
            return False
        return None
