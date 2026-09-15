import json
import time
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import hosted_agenda as pilot

CONFIG = {
    "HUB_AGENDA_READ_ENABLED": "true",
    "HUB_AGENDA_SPREADSHEET_ID": "fictional_spreadsheet_12345",
    "HUB_IAP_AUDIENCE": "/projects/123/locations/us-central1/services/test-hub",
    "HUB_READER_SERVICE_ACCOUNT": "reader@fictional.iam.gserviceaccount.com",
    "HUB_ALLOWED_EMAILS": "teacher@example.com",
}


class ConnectionTests(unittest.TestCase):
    def test_web_route_requires_signed_identity_and_head_does_not_read(self):
        from tests.test_web_hosting import request
        with patch.dict("os.environ", CONFIG, clear=True), patch.object(pilot, "service_token") as token:
            response = request("/api/agenda-connection", HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL="teacher@example.com")
            self.assertEqual(response["status"], "403 Forbidden")
            token.assert_not_called()
        with patch.object(pilot, "check_connection") as check:
            request("/api/agenda-connection", "HEAD")
            request("/api/agenda-connection", "POST")
            check.assert_not_called()

    def test_unconfigured_does_not_authenticate_or_read(self):
        with patch.object(pilot, "authorize") as auth:
            with self.assertRaisesRegex(pilot.ConnectionError, "connection_not_configured"):
                pilot.check_connection("token", {})
            auth.assert_not_called()

    def test_missing_iap_identity_never_gets_runtime_token(self):
        with patch.object(pilot, "service_token") as credential:
            with self.assertRaisesRegex(pilot.ConnectionError, "sign_in_required"):
                pilot.check_connection(None, CONFIG)
            credential.assert_not_called()

    def test_authenticated_metadata_only_no_source_identifiers_in_result(self):
        with patch.object(pilot, "authorize"), patch.object(pilot, "service_token", return_value="fictional-token"), patch.object(pilot, "SheetsReader") as reader:
            reader.return_value.read.return_value = {"sheets": [{}, {}]}
            result = pilot.check_connection("assertion", CONFIG)
            reader.return_value.read.assert_called_once_with(CONFIG["HUB_AGENDA_SPREADSHEET_ID"])
            self.assertEqual(result["tab_count"], 2)
            self.assertFalse(result["cell_data_loaded"])
            self.assertFalse(result["save_enabled"])
            self.assertNotIn("fictional", json.dumps(result))

    def test_provider_failure_is_redacted(self):
        with patch.object(pilot, "authorize"), patch.object(pilot, "service_token", return_value="fictional-token"), patch.object(pilot, "SheetsReader") as reader:
            reader.return_value.read.side_effect = pilot.SheetsReadError("private-content")
            with self.assertRaisesRegex(pilot.ConnectionError, "^agenda_read_failed$"):
                pilot.check_connection("assertion", CONFIG)


try:
    from google.auth import jwt
    from google.auth.crypt import es256
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    HOSTING_DEPS = True
except ImportError:
    HOSTING_DEPS = False


@unittest.skipUnless(HOSTING_DEPS, "Install requirements-hosting.txt for signature tests")
class SignedIdentityTests(unittest.TestCase):
    def setUp(self):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        self.signer = es256.ES256Signer.from_string(private, key_id="test-key")
        self.certs = public.decode()
        self.claims = {"iss": "https://cloud.google.com/iap", "aud": CONFIG["HUB_IAP_AUDIENCE"],
                       "sub": "accounts.google.com:synthetic", "email": "teacher@example.com",
                       "iat": int(time.time()), "exp": int(time.time()) + 120}

    def verify(self, claims, signer=None):
        assertion = jwt.encode(signer or self.signer, claims).decode()
        def transport(url, **kwargs):
            self.assertEqual(url, pilot.IAP_CERTS)
            class Response:
                status = 200
                data = json.dumps({"test-key": self.certs}).encode()
            return Response()
        pilot.authorize(assertion, CONFIG["HUB_IAP_AUDIENCE"], {"teacher@example.com"}, transport)

    def test_valid_signed_teacher(self):
        self.verify(self.claims)

    def test_wrong_audience_issuer_user_expired_or_future_rejected(self):
        for change in ({"aud": "/projects/999/other"}, {"iss": "attacker"},
                       {"email": "other@example.com"}, {"sub": ""},
                       {"exp": int(time.time()) - 60}, {"iat": int(time.time()) + 300}):
            with self.subTest(change=change), self.assertRaisesRegex(pilot.ConnectionError, "^sign_in_required$"):
                self.verify({**self.claims, **change})

    def test_bad_signature_rejected(self):
        other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        with self.assertRaisesRegex(pilot.ConnectionError, "^sign_in_required$"):
            self.verify(self.claims, es256.ES256Signer.from_string(other, key_id="test-key"))

    def test_runtime_identity_mismatch_rejected(self):
        with patch("google.auth.compute_engine.Credentials") as credential, patch.object(pilot, "_request"):
            credential.return_value.service_account_email = "wrong@example.com"
            with self.assertRaisesRegex(pilot.ConnectionError, "^runtime_identity_unavailable$"):
                pilot.service_token(CONFIG["HUB_READER_SERVICE_ACCOUNT"])
