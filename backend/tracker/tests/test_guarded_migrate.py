from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase


class GuardedMigrateTests(SimpleTestCase):
    def setUp(self):
        self.connection = type("Connection", (), {"settings_dict": {"NAME": "scratch_db", "HOST": "db-test"}})()
        self.connections = {"default": self.connection}

    def call_guard(self, **overrides):
        options = {
            "database": "default",
            "expected_database": "scratch_db",
            "expected_host": "db-test",
            "confirm_target": "scratch_db@db-test",
            "app_label": None,
            "target": None,
        }
        options.update(overrides)
        with patch("tracker.management.commands.guarded_migrate.connections", self.connections), \
             patch("tracker.management.commands.guarded_migrate.call_command") as apply:
            call_command("guarded_migrate", **options)
        return apply

    def test_exact_target_and_confirmation_apply_only_requested_migration(self):
        apply = self.call_guard(app_label="tracker", target="0050")
        apply.assert_called_once_with("migrate", "tracker", "0050", database="default", interactive=False)

    def test_wrong_database_name_fails_closed(self):
        with self.assertRaisesMessage(CommandError, "Database name mismatch"):
            self.call_guard(expected_database="tracker")

    def test_wrong_host_fails_closed(self):
        with self.assertRaisesMessage(CommandError, "Database host mismatch"):
            self.call_guard(expected_host="db")

    def test_missing_exact_target_confirmation_fails_closed(self):
        with self.assertRaisesMessage(CommandError, "Confirmation must equal"):
            self.call_guard(confirm_target="yes")

    def test_partial_migration_target_fails_closed(self):
        with self.assertRaisesMessage(CommandError, "must be supplied together"):
            self.call_guard(target="0050")


class RuntimeEntrypointSafetyTests(SimpleTestCase):
    def test_backend_entrypoint_checks_schema_without_applying_migrations(self):
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "entrypoint.sh"
        content = script.read_text(encoding="utf-8")
        self.assertIn("python manage.py migrate --check", content)
        self.assertNotIn("python manage.py migrate\n", content)

    def test_guarded_migration_service_requires_explicit_target_inputs(self):
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "guarded-migrate-entrypoint.sh"
        content = script.read_text(encoding="utf-8")
        for required in (
            "EXPECTED_DATABASE_NAME",
            "EXPECTED_DATABASE_HOST",
            "CONFIRM_MIGRATION_TARGET",
            "guarded_migrate",
        ):
            self.assertIn(required, content)

    def test_compose_backend_uses_host_mounted_runtime_guard(self):
        from pathlib import Path

        config = Path("/app/docker-compose.yml")
        content = config.read_text(encoding="utf-8")
        self.assertIn(
            'entrypoint: ["/bin/sh", "/runtime-guard-entrypoint.sh"]', content
        )
        self.assertIn(
            "./backend/entrypoint.sh:/runtime-guard-entrypoint.sh:ro", content
        )
        # Compose's backend must not fall back to an image-only entrypoint:
        # old images contained a startup script that ran `migrate` directly.
        self.assertNotIn('entrypoint: ["/entrypoint.sh"]', content)

    def test_backend_test_service_uses_a_separate_ephemeral_database(self):
        from pathlib import Path

        config = Path(__file__).resolve().parents[2] / "docker-compose.test.yml"
        content = config.read_text(encoding="utf-8")
        self.assertIn("POSTGRES_DB: wtdwi_test", content)
        self.assertIn("POSTGRES_HOST: db-test", content)
        self.assertIn("tmpfs:", content)
        self.assertIn('entrypoint: ["python"]', content)
