import subprocess
from src.utils.logger import get_logger


logger = get_logger(__name__)


def git_get_branch_and_hash() -> tuple[str, str] | tuple[None, None]:
    try:
        branch_name = (
            subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
            .strip()
            .decode("utf-8")
        )
        commit_hash = (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
            .strip()
            .decode("utf-8")
        )
        return branch_name, commit_hash
    except subprocess.CalledProcessError as e:
        logger.warning(f"An error occurred while running git commands: {e}")
        return None, None
