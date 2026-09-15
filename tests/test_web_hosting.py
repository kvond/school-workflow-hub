import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from school_workflow_hub.web import application, ASSETS, ASSET_ROOT


def request(path="/", method="GET", **extra):
    result = {}
    def start(status, headers):
        result.update(status=status, headers=dict(headers))
    result["body"] = b"".join(application(
        {"PATH_INFO": path, "REQUEST_METHOD": method, **extra}, start))
    return result


class HostingTests(TestCase):
    def test_dashboard_assets_are_packaged_and_unchanged(self):
        manifest = json.loads((ASSET_ROOT.parent / "docs" /
                               "FRONTEND_PREVIEW_SHA256.json").read_text())
        self.assertEqual(set(manifest), set(ASSETS))
        for name, expected in manifest.items():
            response = request("/" + name)
            self.assertEqual(response["status"], "200 OK", name)
            self.assertEqual(hashlib.sha256(response["body"]).hexdigest(), expected)
        self.assertIn(b"Design preview", request()["body"])

    def test_health_is_not_live_authentication_or_write_verification(self):
        self.assertEqual(request("/healthz")["status"], "200 OK")
        payload = json.loads(request("/api/readiness")["body"])
        for key in ("school_connected", "authentication_verified",
                    "persistent_storage_verified", "save_enabled", "run_enabled"):
            self.assertIs(payload[key], False)

    def test_write_methods_never_execute(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            for path in ("/", "/api/save", "/api/run", "/auth/callback", "/healthz"):
                response = request(path, method)
                self.assertEqual(response["status"], "405 Method Not Allowed")

    def test_unknown_routes_and_private_files_never_served(self):
        for path in ("/.env", "/Dockerfile", "/src/school_workflow_hub/web.py",
                     "/../pyproject.toml", "/%2e%2e/pyproject.toml", "//index.html",
                     "/web/index.html", "/private/credentials.json", "/index.html/",
                     "/.git/config", "/index.html\x00", "/%69ndex.html"):
            self.assertEqual(request(path)["status"], "404 Not Found", path)

    def test_no_auth_callback_or_live_api_claims(self):
        for path in ("/api/agenda", "/api/save", "/api/run", "/auth/callback",
                     "/oauth/callback"):
            response = request(path, QUERY_STRING="code=secret-placeholder",
                               HTTP_AUTHORIZATION="Bearer secret-placeholder",
                               HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL="untrusted")
            self.assertEqual(response["status"], "503 Service Unavailable")
            self.assertNotIn(b"secret-placeholder", response["body"])
            self.assertNotIn(b"untrusted", response["body"])

    def test_missing_assets_do_not_disclose_paths(self):
        with patch.object(Path, "read_bytes", side_effect=OSError("private-detail")):
            response = request()
        self.assertEqual(response["status"], "503 Service Unavailable")
        self.assertNotIn(b"private-detail", response["body"])

    def test_head_and_security_headers(self):
        for path in ("/", "/healthz", "/api/agenda", "/absent"):
            get = request(path)
            head = request(path, "HEAD")
            self.assertEqual(head["body"], b"")
            self.assertEqual(head["headers"], get["headers"])
            self.assertEqual(get["headers"]["Cache-Control"], "no-store")
            self.assertEqual(get["headers"]["X-Content-Type-Options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", get["headers"]["Content-Security-Policy"])
