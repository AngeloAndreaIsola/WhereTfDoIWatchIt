import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from availability.services.experiment_runner import ExperimentBlocked, validate_manifest


# Keep the approved, secret-free manifest inside the backend build context.
# Repository-level docs are intentionally not copied into the runtime image.
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "tests/fixtures/stage2e_manifest.example.json"


class ExperimentCommand(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))

    def load_manifest(self, path):
        try:
            manifest = json.loads(Path(path).read_text(encoding="utf-8"))
            validate_manifest(manifest)
            return manifest
        except (OSError, ValueError, ExperimentBlocked) as exc:
            raise CommandError(f"Invalid experiment manifest: {exc}") from exc
