import unittest
from threading import Event
from unittest.mock import Mock, patch

from biweekly_bills.link_server import create_app
from biweekly_bills.settings import Settings


def settings(environment="sandbox"):
    return Settings(
        client_id="test-client",
        secret="test-secret",
        environment=environment,
        redirect_uri=None,
        host="127.0.0.1",
        port=8000,
    )


class LinkServerTests(unittest.TestCase):
    def test_link_page_keeps_javascript_newline_escapes(self):
        with patch("biweekly_bills.link_server.preflight_store"), patch(
            "biweekly_bills.link_server.build_client", return_value=Mock()
        ):
            app = create_app(settings())
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("document.getElementById('link').onclick=openLink", html)
        self.assertIn("persisted locally.\\nItem ID:", html)

    def test_cancel_endpoint_releases_desktop_link_waiter(self):
        completion = Event()
        with patch("biweekly_bills.link_server.preflight_store"), patch(
            "biweekly_bills.link_server.build_client",
            return_value=Mock(),
        ):
            app = create_app(
                settings(),
                completion_event=completion,
            )
        client = app.test_client()
        response = client.post("/api/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(completion.is_set())

    def test_update_mode_uses_existing_token_and_disables_exchange(self):
        completion = Event()
        with patch("biweekly_bills.link_server.preflight_store"), patch(
            "biweekly_bills.link_server.build_client", return_value=Mock()
        ), patch(
            "biweekly_bills.link_server.create_update_link_token",
            return_value={"link_token": "update-token"},
        ) as update_token, patch(
            "biweekly_bills.link_server.create_link_token"
        ) as initial_token:
            app = create_app(
                settings(),
                update_mode=True,
                access_token="existing-access-token",
                completion_event=completion,
            )
            client = app.test_client()
            response = client.post("/api/link-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["link_token"], "update-token")
        update_token.assert_called_once()
        initial_token.assert_not_called()

        exchange = client.post("/api/exchange", json={"public_token": "should-not-be-used"})
        self.assertEqual(exchange.status_code, 409)

    def test_production_initial_link_requires_guarded_session_reservation(self):
        with patch("biweekly_bills.link_server.preflight_store"), patch(
            "biweekly_bills.link_server.initial_link_session_reserved",
            return_value=False,
        ), patch(
            "biweekly_bills.link_server.build_client"
        ) as build_client:
            with self.assertRaisesRegex(RuntimeError, "one-session workflow"):
                create_app(settings("production"))
            build_client.assert_not_called()

    def test_production_exchange_writes_recovery_guard_before_final_save(self):
        completion = Event()
        call_order = []

        def mark_pending(**kwargs):
            call_order.append("pending")

        def mark_lock(item_id):
            call_order.append("lock")

        def save_final(**kwargs):
            call_order.append("final")

        with patch("biweekly_bills.link_server.preflight_store"), patch(
            "biweekly_bills.link_server.initial_link_session_reserved",
            return_value=True,
        ), patch(
            "biweekly_bills.link_server.assert_initial_production_link_allowed"
        ), patch(
            "biweekly_bills.link_server.build_client", return_value=Mock()
        ), patch(
            "biweekly_bills.link_server.exchange_public_token",
            return_value={"access_token": "prod-token", "item_id": "prod-item"},
        ), patch(
            "biweekly_bills.link_server.save_pending_production_exchange",
            side_effect=mark_pending,
        ), patch(
            "biweekly_bills.link_server.mark_production_item_created",
            side_effect=mark_lock,
        ), patch(
            "biweekly_bills.link_server.save_credentials",
            side_effect=save_final,
        ), patch(
            "biweekly_bills.link_server.clear_pending_production_exchange"
        ), patch(
            "biweekly_bills.link_server.release_initial_link_session"
        ):
            app = create_app(settings("production"), completion_event=completion)
            client = app.test_client()
            response = client.post("/api/exchange", json={"public_token": "public-token"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(call_order[:3], ["lock", "pending", "final"])
        self.assertTrue(completion.is_set())


if __name__ == "__main__":
    unittest.main()
