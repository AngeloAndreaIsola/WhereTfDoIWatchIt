import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from availability.services.issue97_collection import collection_preflight, load_cohort, replay_scope


class Command(BaseCommand):
    help = "Prepare the NL cohort offline and optionally replay synthetic JustWatch fixtures."

    def add_arguments(self, parser):
        parser.add_argument("--cohort", type=Path, required=True)
        parser.add_argument("--identities", type=Path, required=True)
        parser.add_argument("--synthetic-fixtures", type=Path)

    def handle(self, *args, **options):
        try:
            scopes = load_cohort(options["cohort"], options["identities"])
            report = collection_preflight(scopes)
            if path := options["synthetic_fixtures"]:
                fixtures = json.loads(path.read_text(encoding="utf-8"))
                by_id = {scope["scope_id"]: scope for scope in scopes}
                report["fixture_replays"] = [
                    replay_scope(by_id[scope_id], fixture["search"], seasons_response=fixture.get("seasons"))
                    for scope_id, fixture in fixtures.items()
                ]
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise CommandError(f"Invalid offline collection input: {exc}") from exc
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
