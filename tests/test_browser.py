"""Opt-in real Chromium test against two HTTPS origins and mocked GitHub."""
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.main import create_app
from scripts.build_pages import build
from tests.test_remote import TOKEN_A, TOKEN_B, Upstream, config
from tests.test_explorer import fork

pytestmark = pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="opt-in HTTPS browser test")


def certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "localhost.crt", tmp_path / "localhost.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


def listen():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def serve(app, sock, cert, key):
    server = uvicorn.Server(uvicorn.Config(app, ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    return server, thread


def test_https_pages_cross_origin_lifecycle(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    cert, key = certificate(tmp_path)
    api_sock, pages_sock = listen(), listen()
    api_origin = f"https://localhost:{api_sock.getsockname()[1]}"
    pages_origin = f"https://localhost:{pages_sock.getsockname()[1]}"
    site = tmp_path / "site"
    build(api_origin, site)
    upstream = Upstream()
    upstream.pages = [[fork(i) for i in range(1, 56)]]
    for row in upstream.pages[0]:
        row["owner"]["websiteUrl"] = "https://example.com"
    app = create_app(config(tmp_path, allowed_origins=[pages_origin]), httpx.MockTransport(upstream))
    pages_app = FastAPI()
    pages_app.mount("/Classmates-Explorer", StaticFiles(directory=site, html=True))
    servers = [serve(app, api_sock, cert, key), serve(pages_app, pages_sock, cert, key)]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
            ctx = browser.new_context(ignore_https_errors=True, accept_downloads=True)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(pages_origin + "/Classmates-Explorer/")
            expect(page.locator("#submit")).to_be_enabled()
            expect(page.locator(".mode-setting")).to_be_hidden()
            page.locator("#token-config summary").click()
            page.locator("#github-token").fill(TOKEN_A)
            page.locator("#save-token").click()
            expect(page.locator("#token-status")).to_have_text("Token Configured")
            page.locator("#repo-url").fill("https://github.com/up/repo")
            page.locator("#submit").click()
            expect(page.locator("#task-title")).to_have_text("Completed", timeout=15000)
            expect(page.locator("#results > tr")).to_have_count(50)
            page.locator("#next").click()
            expect(page.locator("#results > tr")).to_have_count(5)
            page.locator("#sort").select_option("stars:asc")
            page.locator("#search").fill("u55")
            expect(page.locator("#results > tr")).to_have_count(1)
            expect(page.locator("#results .rank-badge")).to_have_text("#55")
            page.locator("#sort").select_option("stars:desc")
            expect(page.locator("#results .rank-badge")).to_have_text("#1")
            page.locator("#search").fill("")
            expect(page.locator("#results > tr")).to_have_count(50)
            page.locator("#history-refresh").click()
            expect(page.locator("#history-select option")).to_have_count(2)
            with page.expect_download() as download:
                page.locator("#export-json").click()
            path = download.value.path()
            assert TOKEN_A not in path.read_text(encoding="utf-8")
            page.locator("#import-file").set_input_files(path)
            expect(page.locator("#task-notice")).to_contain_text("snapshot")
            stored = page.evaluate("JSON.stringify(localStorage)")
            assert TOKEN_A not in stored and TOKEN_B not in stored
            # Separate browser session sees no tasks for another token.
            ctx_b = browser.new_context(ignore_https_errors=True)
            other = ctx_b.new_page()
            other.goto(pages_origin + "/Classmates-Explorer/")
            expect(other.locator("#submit")).to_be_enabled()
            other.locator("#token-config summary").click()
            other.locator("#github-token").fill(TOKEN_B)
            other.locator("#save-token").click()
            expect(other.locator("#token-status")).to_have_text("Token Configured")
            expect(other.locator("#history-select option")).to_have_count(1)
            # Reload retains browser session token and establishes a fresh app session.
            page.reload()
            expect(page.locator("#token-status")).to_have_text("Token Configured")
            expect(page.locator("#task-title")).to_have_text("Completed")
            page.locator("#token-config summary").click()
            page.locator("#logout").click()
            expect(page.locator("#token-status")).to_have_text("Not configured")
            assert TOKEN_A not in page.evaluate("JSON.stringify(sessionStorage)")
            expect(page.locator("#task-panel")).to_be_hidden()
            assert not errors
            browser.close()
    finally:
        for server, thread in servers:
            server.should_exit = True
            thread.join(timeout=10)
        api_sock.close()
        pages_sock.close()
