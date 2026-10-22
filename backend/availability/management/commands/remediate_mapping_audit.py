"""``remediate_mapping_audit`` — two-phase, audited removal of bad mappings.

This command is the *only* supported way to apply the historical confidence-0.7
remediation, and it will not run blind:

* the default mode is a read-only dry run that validates the approved plan
  against the database and prints a manifest;
* ``--write-manifest PATH`` persists that manifest for review;
* ``--manifest PATH`` alone is a validation-only pre-flight;
* ``--manifest PATH --apply --confirm TICKET@DIGEST_PREFIX`` consumes that
  exact manifest and mutates inside one transaction while holding the exclusive
  availability write barrier.

The heavy lifting (plan, manifest, digest, validation, mutation, invariants,
post-commit checks) lives in
:mod:`availability.services.mapping_remediation`; this module is the operator
surface: argument gating, logging and readable output.
"""
from __future__ import annotations

import json
import logging

from django.core.management.base import BaseCommand, CommandError

from availability.services import mapping_remediation
from availability.services.write_barrier import other_advisory_locks

logger = logging.getLogger(__name__)


def _ok(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


class Command(BaseCommand):
    help = (
        "Generate, review and apply the guarded historical confidence-0.7 "
        "mapping remediation. Dry-run by default; mutation needs an approved "
        "manifest plus --apply --confirm TICKET@DIGEST_PREFIX."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Consume --manifest and perform the mutation. Default is dry run.",
        )
        parser.add_argument(
            "--manifest",
            default=None,
            metavar="PATH",
            help="Approved manifest to consume (with --apply) or validate only.",
        )
        parser.add_argument(
            "--confirm",
            default=None,
            metavar="TICKET@DIGEST_PREFIX",
            help=(
                "Required with --apply: a meaningful ticket id plus '@' and the "
                "first 12 hex characters of the reviewed manifest digest "
                "(e.g. AVAIL-1234@0123456789ab)."
            ),
        )
        parser.add_argument(
            "--write-manifest",
            default=None,
            metavar="PATH",
            help="Dry run that also writes the generated manifest to PATH.",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        manifest_path = options["manifest"]
        confirm = options["confirm"]
        write_path = options["write_manifest"]

        if write_path and manifest_path:
            raise CommandError(
                "--write-manifest and --manifest are mutually exclusive"
            )
        if apply and not manifest_path:
            raise CommandError(
                "--apply requires --manifest PATH (the reviewed manifest)"
            )
        if apply and not confirm:
            raise CommandError("--apply requires --confirm TICKET")
        if confirm and not apply:
            raise CommandError("--confirm is only valid together with --apply")

        if manifest_path:
            self._consume_manifest(manifest_path, apply=apply, confirm=confirm)
        else:
            self._generate_manifest(write_path=write_path)

    # -- generation (read-only) ---------------------------------------------

    def _generate_manifest(self, *, write_path):
        try:
            manifest = mapping_remediation.build_manifest()
        except mapping_remediation.RemediationError as exc:
            raise CommandError(f"cannot generate a manifest: {exc}")

        matcher = mapping_remediation.verify_local_matcher_fix()
        barrier = mapping_remediation.verify_local_writer_barrier()
        locks = other_advisory_locks()
        counts = manifest["plan"]["expected_counts"]
        database = manifest["database"]

        self.stdout.write("mode                  : DRY RUN (read-only)")
        self.stdout.write(f"manifest schema       : {manifest['schema']}")
        self.stdout.write(
            "database              : "
            f"{database['vendor']}:{database['name']} "
            f"(strength={database['strength']})"
        )
        self.stdout.write(
            "configured host/port  : "
            f"{database['configured']['host'] or '(default)'}:"
            f"{database['configured']['port'] or '(default)'}"
        )
        if database["server"]:
            server = database["server"]
            self.stdout.write(
                "server address/port   : "
                f"{server['address'] or '(unix socket/unknown)'}:"
                f"{server['port'] if server['port'] is not None else '(unknown)'}"
            )
            self.stdout.write(
                "cluster identifier    : "
                f"{server['system_identifier'] or '(not readable)'}"
            )
        self.stdout.write(f"target mappings       : {counts['mappings']}")
        self.stdout.write(f"target offers         : {counts['offers']}")
        self.stdout.write(f"target evidence       : {counts['evidence']}")
        self.stdout.write(f"target offer prices   : {counts['offer_prices']}")
        self.stdout.write(f"protected mappings    : {counts['protected_mappings']}")
        self.stdout.write(f"local matcher fix     : {'present' if matcher else 'ABSENT'}")
        self.stdout.write(
            f"writer barrier code   : {'present' if barrier else 'ABSENT'}"
        )
        self.stdout.write(f"other advisory locks  : {locks}")
        self.stdout.write(f"manifest digest       : {manifest['digest']}")
        self.stdout.write(
            "confirmation prefix   : "
            f"{mapping_remediation.confirmation_digest_prefix(manifest['digest'])}"
        )
        self.stdout.write(
            "confirmation syntax   : <ticket>@<12-hex digest prefix>"
        )


        if write_path:
            try:
                written = mapping_remediation.write_manifest(write_path, manifest)
            except OSError as exc:
                raise CommandError(f"could not write manifest to {write_path}: {exc}")
            self.stdout.write(
                self.style.SUCCESS(f"manifest written      : {written}")
            )
            self.stdout.write(
                "Review the manifest, then run: "
                "--manifest <path> --apply "
                "--confirm <ticket>@<12-hex digest prefix>"
            )
        else:
            self.stdout.write("")
            self.stdout.write(json.dumps(manifest, indent=2, sort_keys=True))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("No database rows were changed."))

    # -- validation / mutation ----------------------------------------------

    def _consume_manifest(self, manifest_path, *, apply, confirm):
        try:
            manifest = mapping_remediation.load_manifest(manifest_path)
            mapping_remediation.validate_manifest(manifest)
            mapping_remediation.revalidate_targets(manifest)
        except mapping_remediation.ManifestError as exc:
            raise CommandError(f"manifest rejected: {exc}")
        except mapping_remediation.RemediationError as exc:
            raise CommandError(f"manifest does not match the database: {exc}")

        prefix = mapping_remediation.confirmation_digest_prefix(
            manifest["digest"]
        )
        self.stdout.write(f"manifest              : {manifest_path}")
        self.stdout.write(f"manifest digest       : {manifest['digest']}")
        self.stdout.write(f"confirmation prefix   : {prefix}")
        self.stdout.write(
            "confirmation syntax   : <ticket>@<12-hex digest prefix>"
        )
        self.stdout.write(
            "manifest validation   : OK (schema, digest, plan, database, rows)"
        )

        if not apply:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    "Validation only (no --apply): nothing was written."
                )
            )
            return

        try:
            confirmed = mapping_remediation.validate_confirmation(
                confirm, manifest_digest=manifest["digest"]
            )
        except mapping_remediation.RemediationError as exc:
            raise CommandError(str(exc))

        if not mapping_remediation.verify_local_matcher_fix():
            raise CommandError(
                "refusing: the fixed movie matcher is not present in this "
                "process's code. This check covers only the applying process; "
                "verify backend, worker and catalogue-worker separately (see "
                "the runbook) and deploy the fix before retrying."
            )
        if not mapping_remediation.verify_local_writer_barrier():
            raise CommandError(
                "refusing: this process does not carry the shared refresh "
                "write-barrier code. Deploy the same image to every worker and "
                "cron entry point before retrying."
            )

        try:
            barrier = mapping_remediation.acquire_maintenance_barrier()
        except Exception as exc:
            raise CommandError(
                "refusing: could not acquire the exclusive availability write "
                f"barrier ({exc}). Stop worker, catalogue-worker and every cron "
                "entry point, then retry."
            )

        try:
            if (
                not barrier.verified
                and not mapping_remediation.unverified_barrier_allowed()
            ):
                raise CommandError(
                    "refusing: this backend cannot prove that availability "
                    "refresh writers are excluded. Run against PostgreSQL with "
                    "every writer on this image."
                )
            self.stdout.write(f"operator confirmation : {confirmed}")
            self.stdout.write(
                "write barrier         : "
                + (
                    "exclusive lock acquired"
                    if barrier.verified
                    else "UNVERIFIED (test-only setting)"
                )
            )
            try:
                report = mapping_remediation.apply_remediation(
                    manifest, barrier=barrier
                )
            except mapping_remediation.RemediationError as exc:
                raise CommandError(f"mutation aborted and rolled back: {exc}")

            counts = report["counts"]
            logger.info(
                "remediate_mapping_audit applied digest=%s confirm=%s updated=%s "
                "deleted_offers=%s deleted_mappings=%s deleted_evidence=%s",
                manifest["digest"],
                confirmed,
                counts["updated_mapping"],
                counts["deleted_offers"],
                counts["deleted_mappings"],
                counts["deleted_evidence"],
            )

            self.stdout.write("")
            self.stdout.write("In-transaction checks (before commit):")
            self._print_checks(report["checks"])
            self.stdout.write(
                "Transaction committed. Ordinary rollback is no longer "
                "available; post-commit validation is still running."
            )

            try:
                post_checks = mapping_remediation.post_commit_checks(manifest)
            except Exception as exc:
                # The commit already happened. A validation *error* is just as
                # serious as a failed check, and must never look like a safe,
                # retryable pre-commit abort.
                logger.exception(
                    "remediate_mapping_audit post-commit validation crashed "
                    "digest=%s",
                    manifest["digest"],
                )
                self._report_committed_failure(
                    "POST-COMMIT VALIDATION COULD NOT COMPLETE: "
                    f"{type(exc).__name__} (see database/application logs)"
                )
                raise CommandError(
                    "post-commit validation could not complete: "
                    f"{type(exc).__name__}"
                ) from exc

            failed = [check for check in post_checks if not check["ok"]]
            self.stdout.write("")
            self.stdout.write("Post-commit checks (committed state + read path):")
            self._print_checks(post_checks)

            logger.info(
                "remediate_mapping_audit validation digest=%s in_transaction=%s "
                "failed=%s post_commit=%s post_failed=%s",
                manifest["digest"],
                len(report["checks"]),
                [check["name"] for check in report["checks"] if not check["ok"]],
                len(post_checks),
                [check["name"] for check in failed],
            )

            if failed:
                names = ", ".join(check["name"] for check in failed)
                self._report_committed_failure(
                    f"POST-COMMIT VALIDATION FAILED: {names}"
                )
                raise CommandError(
                    "post-commit validation failed: " + names
                )

            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    "Mutation committed and post-commit validation passed."
                )
            )
        finally:
            barrier.release()


    def _print_checks(self, checks):
        for check in checks:
            line = f"  [{_ok(check['ok'])}] {check['name']}"
            if check["detail"]:
                line += f" — {check['detail']}"
            self.stdout.write(line)

    def _report_committed_failure(self, heading):
        """Explain an already-committed failure without implying rollback."""
        self.stdout.write("")
        self.stderr.write(self.style.ERROR(heading))
        self.stderr.write(
            "The transaction is ALREADY COMMITTED. Ordinary transaction "
            "rollback is no longer available."
        )
        self.stderr.write(
            "Keep worker, catalogue-worker and every cron entry point "
            "paused. Preserve this output, the manifest and the database "
            "logs. Do not retry --apply and do not restore a backup "
            "until the documented recovery procedure has been followed."
        )
