from __future__ import absolute_import

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.client import Client


class AuditLogMiddlewareTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="testclient", password="test")
        self.client.force_login(self.user)

    def test_audit_log_middleware_adds_username(self):
        with self.modify_settings(
            MIDDLEWARE={"append": "common.middleware.AuditLogMiddleware"}
        ):
            response = self.client.get("/transfer/")
            self.assertTrue(response.has_header("X-Username"))
            self.assertEqual(response["X-Username"], self.user.username)

    def test_audit_log_middleware_unauthenticated(self):
        with self.modify_settings(
            MIDDLEWARE={"append": "common.middleware.AuditLogMiddleware"}
        ):
            self.client.logout()
            response = self.client.get(settings.LOGIN_URL)
            self.assertTrue(response.has_header("X-Username"))
            self.assertEqual(response["X-Username"], "None")