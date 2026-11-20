"""Protect Movie rows when loading serialized Django fixtures."""

from django.core.management.commands.loaddata import Command as LoadDataCommand
from django.db import connections

from config.test_database import require_disposable_database
from tracker.models import Movie


class Command(LoadDataCommand):
    def loaddata(self, fixture_labels):
        self.movie_target_verified = False
        return super().loaddata(fixture_labels)

    def save_obj(self, obj):
        if isinstance(obj.object, Movie) and not self.movie_target_verified:
            require_disposable_database(connections[self.using])
            self.movie_target_verified = True
        return super().save_obj(obj)
