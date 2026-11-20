from importlib import reload
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from tracker.management.commands import reseed_providers
from tracker.models import ExternalProviderMapping, Provider


class ReseedProvidersTests(TestCase):
    def test_import_and_command_help_do_not_access_the_database(self):
        with self.assertNumQueries(0):
            reload(reseed_providers)
            reseed_providers.Command().create_parser("manage.py", "reseed_providers").format_help()

    def test_explicit_reseed_is_idempotent_and_leaves_unofficial_providers_unchanged(self):
        unofficial_before = list(Provider.objects.filter(category="scraper").order_by("pk").values())
        Provider.objects.filter(slug="prime").delete()
        for _ in range(2):
            call_command("reseed_providers", stdout=StringIO())
        prime = Provider.objects.get(slug="prime")
        self.assertEqual(
            ExternalProviderMapping.objects.get(provider=prime, integration_name="streaming_availability").external_id,
            "prime",
        )
        self.assertEqual(list(Provider.objects.filter(category="scraper").order_by("pk").values()), unofficial_before)
