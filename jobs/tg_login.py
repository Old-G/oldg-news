"""One-off interactive Telegram login. Writes the StringSession into .env as TG_SESSION.

    uv run python -m jobs.tg_login            # local: updates ./.env
    python -m jobs.tg_login --print           # inside a container: prints it, copy into .env yourself

Asks for TG_API_ID / TG_API_HASH when .env has none (https://my.telegram.org → API development
tools), then for phone, login code and the 2FA password. Each machine needs its own session:
one session used from two IPs at once gets revoked by Telegram.
"""

import argparse
import asyncio
import os
import re
from pathlib import Path

from adapters.telegram import make_client

ENV = Path(".env")


def read_env(path: Path, key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    m = re.search(rf"^{key}=(.*)$", path.read_text(), re.M) if path.exists() else None
    return m.group(1).strip() if m else ""


def write_env(path: Path, key: str, value: str) -> None:
    text = path.read_text() if path.exists() else ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^{key}=.*$", re.M)
    text = pattern.sub(lambda _: line, text) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    # .env holds the Telegram session (full account access): owner-only, replaced atomically
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


FORMATS = {
    "TG_API_ID": (r"\d{4,12}", "digits only, e.g. 12345678"),
    "TG_API_HASH": (r"[0-9a-f]{32}", "32 characters 0-9a-f"),
}


def api_credential(key: str, save: bool) -> str:
    pattern, hint = FORMATS[key]
    value = read_env(ENV, key)
    if value and not re.fullmatch(pattern, value):
        print(f"{key} in .env does not look right ({hint}), enter it again")
        value = ""
    while not value:
        value = input(f"{key} (my.telegram.org → API development tools): ").strip()
        if not re.fullmatch(pattern, value):
            print(f"  expected {hint}")
            value = ""
    if save:
        write_env(ENV, key, value)
    return value


def ask_phone() -> str:
    # Telethon treats any input with ':' as a bot token; this login is for the user account only
    while not re.fullmatch(r"\+\d{8,15}", phone := input("Phone in international format, e.g. +375291234567: ").strip().replace(" ", "")):
        print("  expected + and digits; a bot token does not work here")
    return phone


async def main(print_only: bool) -> None:
    api_id = api_credential("TG_API_ID", not print_only)
    api_hash = api_credential("TG_API_HASH", not print_only)
    client = make_client(int(api_id), api_hash)
    await client.start(phone=ask_phone)
    me = await client.get_me()
    session = client.session.save()
    await client.disconnect()
    print(f"Logged in as {me.first_name} (@{me.username}, id {me.id})")
    if print_only:
        print(f"TG_SESSION={session}")
    else:
        write_env(ENV, "TG_SESSION", session)
        print("TG_API_ID, TG_API_HASH and TG_SESSION are in .env")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", dest="print_only", action="store_true")
    asyncio.run(main(parser.parse_args().print_only))
