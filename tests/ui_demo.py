"""Local browser verification with synthetic GitHub data; never uses a real token."""
import tempfile
from pathlib import Path

import httpx
import uvicorn

from app.main import create_app
from tests.test_explorer import FakeGitHub, fork, owner, settings


def main():
    directory = Path(tempfile.mkdtemp(prefix="explorer-ui-"))
    config = settings(directory, max_forks=55)
    config.github_request_interval = 0.3
    pages = [[fork(i, owner(f"U{i}")) for i in range(1, 56)]]
    pages[0][0] = fork(1, owner("Team", "Organization"))
    fake = FakeGitHub(pages)
    app = create_app(config, httpx.MockTransport(fake))
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
