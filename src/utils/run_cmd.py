import asyncio
import subprocess


async def run_cmd_async(
    cmd: str, input: bytes | None = None, log_error_only: bool = False
) -> str:
    # streams output to stdout and stderr
    # with log_error_only the output will only be printed if there's an error
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE if input else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_lines = []
    stderr_lines = []

    while True:
        stdout_line = await process.stdout.readline()
        stderr_line = await process.stderr.readline()

        if stdout_line:
            stdout_lines.append(stdout_line.decode())
            if not log_error_only:
                print(stdout_line.decode(), end="")

        if stderr_line:
            stderr_lines.append(stderr_line.decode())
            if not log_error_only:
                print(stderr_line.decode(), end="")

        if not stdout_line and not stderr_line and process.poll() is not None:
            break

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)

    if process.returncode != 0:
        # Log both stdout and stderr if there's an error
        raise ValueError(f"Command failed: {cmd}\n{stderr}\n{stdout}")

    return stdout
