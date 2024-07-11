import asyncio


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
