import http.client
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import server


class ServerHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_health_and_unknown_route(self):
        status, body, _ = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "running": False})

        status, body, _ = self.request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "not found"})

    def test_scrape_rejects_malformed_and_unknown_requests(self):
        status, body, _ = self.request(
            "POST", "/api/scrape", body=b"{", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid JSON body"})

        status, body, _ = self.request(
            "POST", "/api/scrape", body=b'{"target":"unknown"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("target must be one of", json.loads(body)["error"])

    def test_proxy_validation_and_upstream_http_statuses(self):
        status, body, _ = self.request("GET", "/api/proxy")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "missing url param"})

        status, body, _ = self.request(
            "GET", "/api/proxy?url=ftp%3A%2F%2Fexample.invalid%2Ffeed"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "only http/https urls may be proxied"})

        status, body, _ = self.request(
            "GET", "/api/proxy?url=http%3A%2F%2F127.0.0.1%2F"
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "host not allowed"})

        target = "http://upstream.invalid/camera.jpg"
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(upstream_status=code):
                error = urllib.error.HTTPError(
                    target, code, "controlled response", {}, io.BytesIO(b"")
                )
                try:
                    with (
                        mock.patch.object(server, "_proxy_host_allowed", return_value=True),
                        mock.patch.object(server, "_proxy_open", side_effect=error),
                    ):
                        status, body, headers = self.request(
                            "GET", "/api/proxy?" + urllib.parse.urlencode({"url": target})
                        )
                finally:
                    error.close()
                self.assertEqual(status, code)
                self.assertEqual(json.loads(body), {"error": f"upstream {code}"})
                self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")

        with (
            mock.patch.object(server, "_proxy_host_allowed", return_value=True),
            mock.patch.object(
                server, "_proxy_open",
                side_effect=urllib.error.URLError("controlled timeout"),
            ),
        ):
            status, body, _ = self.request(
                "GET", "/api/proxy?" + urllib.parse.urlencode({"url": target})
            )
        self.assertEqual(status, 502)
        self.assertIn("proxy fetch failed", json.loads(body)["error"])

    def test_redirect_chain_is_revalidated_at_every_hop(self):
        private_hits = []

        class FinalHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                private_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"private")

        final_server = ThreadingHTTPServer(("127.0.0.1", 0), FinalHandler)

        class MiddleHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{final_server.server_address[1]}/private",
                )
                self.end_headers()

        middle_server = ThreadingHTTPServer(("127.0.0.1", 0), MiddleHandler)

        class FirstHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{middle_server.server_address[1]}/middle",
                )
                self.end_headers()

        first_server = ThreadingHTTPServer(("127.0.0.1", 0), FirstHandler)
        servers = [final_server, middle_server, first_server]
        threads = [
            threading.Thread(target=local.serve_forever, daemon=True)
            for local in servers
        ]
        for thread in threads:
            thread.start()
        try:
            target = f"http://127.0.0.1:{first_server.server_address[1]}/first"
            with urllib.request.urlopen(target, timeout=2) as legacy_response:
                self.assertEqual(legacy_response.status, 200)
                self.assertEqual(legacy_response.read(), b"private")
            self.assertEqual(private_hits, ["/private"])
            private_hits.clear()

            with mock.patch.object(
                server, "_proxy_host_allowed", side_effect=[True, True, False]
            ) as allowed:
                status, body, _ = self.request(
                    "GET", "/api/proxy?" + urllib.parse.urlencode({"url": target})
                )
        finally:
            for local in servers:
                local.shutdown()
                local.server_close()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "redirect host not allowed"})
        self.assertEqual(private_hits, [])
        self.assertEqual(allowed.call_count, 3)

    def test_only_one_scrape_runs_at_a_time(self):
        entered = threading.Event()
        release = threading.Event()
        first_result = []

        def block_scrape(handler, _target):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test scrape was not released")
            handler._json(200, {"finished": True})

        def first_request():
            first_result.append(self.request(
                "POST", "/api/scrape", body=b'{"target":"native"}',
                headers={"Content-Type": "application/json"},
            ))

        with mock.patch.object(server.Handler, "_stream_scrape", new=block_scrape):
            worker = threading.Thread(target=first_request)
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                status, body, _ = self.request(
                    "POST", "/api/scrape", body=b'{"target":"native"}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body), {"error": "a scrape is already running"})
            finally:
                release.set()
                worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(first_result[0][0], 200)
        self.assertFalse(server._run_lock.locked())


if __name__ == "__main__":
    unittest.main()
