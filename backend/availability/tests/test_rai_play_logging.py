"""RaiPlay checkpoint/resume events must be visible in ordinary container logs.

The live resume proof (Phase 3.3H) succeeded while ``rai_play_walk_resume`` was
absent from ``docker compose logs``: Django's stock config filters its console
handler on ``RequireDebugTrue`` and the containers run with ``DJANGO_DEBUG=0``,
so the record reached no handler at all. These tests assert the *effective*
configuration rather than only the settings dictionary: records are emitted
through the same logger the source uses and must land in the configured
handler's stream.

No database and no network: the walk uses the recorded RaiPlay fixture.
"""
import io
import logging
from contextlib import contextmanager

from django.test import SimpleTestCase

from availability.sources import checkpoints
from availability.sources.rai_play import (
    CURSOR_VERSION,
    PART_PROGRAMMES,
    RaiPlayClient,
    RaiPlaySource,
)

from .rai_play_fixtures import FakeRaiPlayApi

RAI_PLAY_LOGGER = "availability.sources.rai_play"


def _source(api, max_contents):
    return RaiPlaySource(client=RaiPlayClient(requester=api), max_contents=max_contents)


@contextmanager
def _capture(*loggers):
    """Swap every configured stream for one buffer, then restore it.

    Capturing all handlers involved (the RaiPlay logger's and the root's) is
    what makes a duplicate line observable instead of invisible on stderr.
    """
    buffer = io.StringIO()
    # The same handler object is shared by the RaiPlay logger and the root
    # logger (one ``console`` entry in LOGGING), so deduplicate before swapping:
    # setting the same stream twice would record ``None`` as the original.
    handlers = list(
        {
            id(handler): handler
            for logger in loggers
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
        }.values()
    )
    originals = [handler.setStream(buffer) for handler in handlers]
    try:
        yield buffer
    finally:
        for handler, original in zip(handlers, originals):
            handler.setStream(original)


class LoggerConfigurationTests(SimpleTestCase):
    def test_rai_play_logger_is_at_info_and_stops_at_its_own_handler(self):
        logger = logging.getLogger(RAI_PLAY_LOGGER)
        self.assertEqual(logger.level, logging.INFO)
        self.assertEqual(logger.getEffectiveLevel(), logging.INFO)
        # Duplication guard: the root handler must not print the same record.
        self.assertFalse(logger.propagate)
        self.assertTrue(logger.handlers)

    def test_unrelated_components_keep_the_warning_default(self):
        for name in (
            "availability.sources.tubi",
            "tracker.services.bulk_refresh",
            "django.request",
            "gunicorn.error",
        ):
            with self.subTest(logger=name):
                self.assertEqual(
                    logging.getLogger(name).getEffectiveLevel(),
                    logging.WARNING,
                )

    def test_the_root_logger_keeps_a_warning_console_handler(self):
        root = logging.getLogger()
        self.assertEqual(root.level, logging.WARNING)
        self.assertTrue(
            any(isinstance(handler, logging.StreamHandler) for handler in root.handlers)
        )

    def test_the_console_handler_is_not_gated_on_debug(self):
        # DJANGO_DEBUG=0 is the deployment default; a RequireDebugTrue filter is
        # exactly what made the event unreachable.
        for handler in logging.getLogger(RAI_PLAY_LOGGER).handlers:
            self.assertNotIn("require_debug_true", handler.filters)


class EventVisibilityTests(SimpleTestCase):
    def test_walk_resume_and_checkpoint_missing_reach_the_configured_handler(self):
        logger = logging.getLogger(RAI_PLAY_LOGGER)
        # First pass leaves a checkpoint; the second pass resumes from it.
        first = _source(FakeRaiPlayApi(), max_contents=1).fetch()
        self.assertIsNotNone(first.cursor)

        api = FakeRaiPlayApi()
        stale = checkpoints.encode(
            CURSOR_VERSION, {PART_PROGRAMMES: "https://www.raiplay.it/programmi/absent"}
        )

        with _capture(logger, logging.getLogger()) as buffer:
            resumed = _source(api, max_contents=1).fetch(cursor=first.cursor)
            _source(FakeRaiPlayApi(), max_contents=1).fetch(cursor=stale)

        output = buffer.getvalue()
        self.assertIsNotNone(resumed.cursor)
        self.assertIn("rai_play_walk_resume", output)
        self.assertIn("after=", output)
        self.assertIn("rai_play_cursor_checkpoint_missing", output)
        self.assertIn("reason=restarting", output)
        # The formatter identifies the emitting logger, so an operator can tell
        # a source event from an unrelated component's line.
        self.assertIn(RAI_PLAY_LOGGER, output)

    def test_a_resume_event_is_written_exactly_once(self):
        first = _source(FakeRaiPlayApi(), max_contents=1).fetch()
        self.assertIsNotNone(first.cursor)

        logger = logging.getLogger(RAI_PLAY_LOGGER)
        with _capture(logger, logging.getLogger()) as buffer:
            _source(FakeRaiPlayApi(), max_contents=1).fetch(cursor=first.cursor)

        self.assertEqual(buffer.getvalue().count("rai_play_walk_resume"), 1)

    def test_an_unrelated_source_info_event_stays_invisible(self):
        # Raising RaiPlay to INFO must not turn every collector's chatter on.
        with _capture(logging.getLogger(RAI_PLAY_LOGGER), logging.getLogger()) as buffer:
            logging.getLogger("availability.sources.tubi").info(
                "tubi_walk_resume after=unexpected"
            )

        self.assertNotIn("tubi_walk_resume", buffer.getvalue())
