import asyncio
import subprocess


async def run_cmd_async(cmd: str, input: bytes | None = None) -> str:
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE if input else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if input is not None:
        stdout, stderr = await process.communicate(input=input)
    else:
        stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
    return stdout.decode()


def run_cmd(cmd: str, input: bytes | None = None) -> str:
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdin=subprocess.PIPE if input else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if input is not None:
        stdout, stderr = process.communicate(input=input)
    else:
        stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
    return stdout.decode()
