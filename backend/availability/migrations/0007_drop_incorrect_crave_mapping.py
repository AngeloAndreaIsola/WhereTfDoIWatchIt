"""Remove the incorrect ``crave -> cbc_gem`` JustWatch provider mapping.

Why a corrective migration rather than editing migration 0006
-------------------------------------------------------------
0006 may already be applied in deployed environments, so editing it would
never re-run there. The mistake is a **data-integrity** bug, not a cosmetic
one: it asserted that JustWatch's ``crave`` package (Crave, the Canadian
streaming service) is the same service as CBC Gem. Both are Canadian, and
neither brand appears in the other name, which is exactly why a
name-similarity or geography-based guess is not acceptable — mapping it would
attach Crave's catalogue to the canonical ``cbc_gem`` provider and write
confidently wrong offers onto the canonical model.

It is removed rather than re-pointed at a new provider: this project has no
canonical ``crave`` provider row, and inventing one here would be a product
decision, not a migration. Until such a provider exists, Crave's offers are
**unmapped and skipped** (logged by the source), which is the safe outcome —
no wrong data, and a visible gap.

Only rows this project is known to own are removed (integration
``justwatch``, external id ``crave``), so a legitimate provider mapping that
somebody added by hand for a different integration is untouched.
"""
from django.db import migrations

JUSTWATCH = "justwatch"
WRONG_EXTERNAL_ID = "crave"
# Canonical slug it was wrongly pointing at.
WRONG_SLUG = "cbc_gem"


def drop_incorrect_crave_mapping(apps, schema_editor):
    ExternalProviderMapping = apps.get_model("tracker", "ExternalProviderMapping")
    Provider = apps.get_model("tracker", "Provider")
    canonical = Provider.objects.filter(slug=WRONG_SLUG).first()
    if canonical is None:
        return
    ExternalProviderMapping.objects.filter(
        provider=canonical,
        integration_name=JUSTWATCH,
        external_id=WRONG_EXTERNAL_ID,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("availability", "0006_seed_aggregator_sources"),
    ]

    operations = [
        migrations.RunPython(drop_incorrect_crave_mapping,
                             migrations.RunPython.noop),
    ]