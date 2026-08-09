from __future__ import annotations

import asyncio
import json

from .config import Settings
from .mailer import GmailSMTPSender


async def _run() -> int:
    result = await GmailSMTPSender(Settings()).self_test()
    print(json.dumps(result.model_dump(mode="json"), indent=2))  # noqa: T201
    return 0 if result.ok else 1


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
