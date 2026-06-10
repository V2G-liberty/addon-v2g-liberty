"""Unit tests for log_wrapper module.

Verifies that the logging wrapper:
- Produces log output via Python's standard logging (caplog)
- Works from worker threads (run_in_executor) without crashing
- Maps level strings correctly
"""

import asyncio
import logging

import pytest

from apps.v2g_liberty.log_wrapper import get_class_method_logger


class TestGetClassMethodLogger:
    def test_info_message(self, caplog):
        log = get_class_method_logger(module_name="test_mod")
        with caplog.at_level(logging.DEBUG):
            log("hello from test")
        assert "hello from test" in caplog.text

    def test_warning_level(self, caplog):
        log = get_class_method_logger(module_name="test_mod")
        with caplog.at_level(logging.DEBUG):
            log("something went wrong", level="WARNING")
        assert "something went wrong" in caplog.text
        assert caplog.records[-1].levelno == logging.WARNING

    def test_empty_level_defaults_to_info(self, caplog):
        log = get_class_method_logger(module_name="test_mod")
        with caplog.at_level(logging.DEBUG):
            log("default level")
        assert caplog.records[-1].levelno == logging.INFO

    def test_invalid_level_defaults_to_info(self, caplog):
        log = get_class_method_logger(module_name="test_mod")
        with caplog.at_level(logging.DEBUG):
            log("bogus level", level="BOGUS")
        assert caplog.records[-1].levelno == logging.INFO

    def test_default_module_name(self, caplog):
        """An empty module_name should still produce a working logger."""
        log = get_class_method_logger()
        with caplog.at_level(logging.DEBUG):
            log("still works")
        assert "still works" in caplog.text

    def test_logger_name_includes_module(self, caplog):
        log = get_class_method_logger(module_name="my_module")
        with caplog.at_level(logging.DEBUG):
            log("check name")
        assert caplog.records[-1].name == "AppDaemon.v2g-app.my_module"


class TestThreadSafety:
    @pytest.mark.asyncio
    async def test_logging_from_executor_thread(self, caplog):
        """Verify that log_wrapper works from a worker thread (run_in_executor).

        This is the core scenario that previously crashed with:
        RuntimeError: Non-thread-safe operation invoked on an event loop
        other than the current one (via AppDaemon's LogSubscriptionHandler).
        """
        log = get_class_method_logger(module_name="test_thread")

        def work_in_thread():
            log("message from worker thread")
            log("warning from worker thread", level="WARNING")

        with caplog.at_level(logging.DEBUG):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, work_in_thread)

        assert "message from worker thread" in caplog.text
        assert "warning from worker thread" in caplog.text
