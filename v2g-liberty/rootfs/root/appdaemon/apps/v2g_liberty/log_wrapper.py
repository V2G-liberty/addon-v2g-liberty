"""Logging wrapper for V2G Liberty modules.

Provides a log function that automatically includes the module name,
line number, and function name of the caller in every log message.

Uses Python's standard ``logging`` module instead of AppDaemon's
``hass.log()`` because:
- ``logging`` is thread-safe (``hass.log`` crashes in ``run_in_executor``)
- Runtime log-level switching is possible via ``logger.setLevel()``
- Per-module log levels are supported

Log output goes to the same file as ``hass.log()`` because AppDaemon
configures a ``RotatingFileHandler`` on the ``"AppDaemon"`` logger, and
our child loggers propagate to it automatically.

Caller info (line number, function name) is embedded in the message
itself because AppDaemon's formatter does not include ``%(lineno)d``
or ``%(funcName)s``.
"""

import logging
import sys


def get_class_method_logger(module_name=""):
    """Create a log function for a module.

    Args:
        module_name: Short module name (e.g. ``"main_app"``), used as the
            logger child name under ``AppDaemon.v2g-app``.

    Returns:
        A callable ``log(msg, level="")`` that logs via Python's ``logging``.
        Each message is prefixed with ``module.lineno > function > ``.
    """
    logger = logging.getLogger(f"AppDaemon.v2g-app.{module_name}")

    def log(msg: str, level: str = ""):
        log_level = getattr(logging, level.upper() if level else "INFO", logging.INFO)
        # Get caller info from the frame above (the actual caller).
        frame = sys._getframe(1)
        lineno = frame.f_lineno
        func_name = frame.f_code.co_name
        formatted = f"{lineno} > {func_name} > {msg}"
        logger.log(log_level, formatted)

    return log
