"""
Run one item-bootstrap command and print its report.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.plan.item_bootstrap.commands import BootstrapCommand
from basstler.plan.item_bootstrap.exceptions import BootstrapError
from basstler.standard_streams import StandardStreamHandler

# %% command line


logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""


def main() -> int:
    """
    Parse arguments, run the requested operation, and print its report.

    :return: The status the process exits with.
    """
    arguments = BootstrapCommand.parser().parse_args()
    project_root = Path(
        os.environ.get(ClaudeCodeVariable.PROJECT_DIRECTORY, Path.cwd())
    )

    command = next(
        command
        for command in BootstrapCommand.every_command()
        if command.invoked_as == arguments.subcommand
    )
    try:
        report = command.run(arguments, project_root)
    except BootstrapError as error:
        logger.error(f"{error.exit_code.name_for_a_caller}: {error}")
        return int(error.exit_code)

    logger.info(json.dumps(report.to_json()))
    return int(report.exit_code)


if __name__ == "__main__":
    sys.exit(main())
