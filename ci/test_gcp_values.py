"""Offline GCP DR values checks; never create a real Argo CD Application."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from test_validate_cd import REPO_ROOT, ValidatorTestCase
from validate_cd import main


class GcpValuesTests(ValidatorTestCase):
    def setUp(self):
        super().setUp()
        self.environment = "gcp"
        self.values_path = self.root / "environments/gcp/values-dr.yaml"
        self.application_path = self.root / "applications/gcp.yaml"
        self.values = self.valid_values("gcp")
        self.values["checkout"]["redis"] = {
            "create": True, "service": {"type": "ClusterIP", "port": 6379},
        }
        self.values["checkout"]["app"]["persistence"]["redis"] = {
            "endpoint": "", "secretName": "", "tls": False,
        }
        self.values["ui"]["app"]["session"] = {
            "type": "redis",
            "redis": {"endpoint": "checkout-redis:6379", "secretName": "", "tls": False},
        }
        self.application = self.valid_application("gcp")
        self.persist()

    def values_check(self, *, files=None, application=False):
        args = ["--environment", "gcp", "--mode", "values"]
        for path in files or [self.values_path, self.versions_path]:
            args.extend(["-f", str(path)])
        if application:
            args.extend(["--application", str(self.application_path)])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(args, repo_root=self.root)
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_internal_redis_accepts_empty_checkout_endpoint(self):
        code, _, stderr = self.invoke()
        self.assertEqual(0, code, stderr)

    def test_values_mode_does_not_require_an_application(self):
        self.application_path.unlink()  # This fixture exists only in TemporaryDirectory.
        code, stdout, stderr = self.values_check()
        self.assertEqual(0, code, stderr)
        self.assertIn("values validation succeeded", stdout)
        self.assertIn("Application and runtime unverified", stdout)
        self.assertNotIn("deployment input validation succeeded", stdout)

    def test_values_mode_rejects_application_instead_of_ignoring_it(self):
        code, _, stderr = self.values_check(application=True)
        self.assertEqual(1, code)
        self.assertIn("--application", stderr)

    def test_deployment_mode_still_requires_application(self):
        code, _, stderr = self.invoke(application=False)
        self.assertEqual(1, code)
        self.assertIn("--application: is required", stderr)

    def test_values_mode_requires_current_path_and_exact_order(self):
        old_path = self.root / "environments/gcp/values.yaml"
        self.write_yaml(old_path, self.values)
        for paths in ([old_path, self.versions_path], [self.versions_path, self.values_path],
                      [self.values_path], [self.values_path, self.versions_path, self.values_path]):
            with self.subTest(paths=paths):
                code, _, stderr = self.values_check(files=paths)
                self.assertEqual(1, code)
                self.assertIn("-f input stack", stderr)

    def test_application_cannot_keep_the_old_gcp_path(self):
        self.application["spec"]["source"]["helm"]["valueFiles"][0] = "../../../environments/gcp/values.yaml"
        self.persist()
        code, _, stderr = self.invoke()
        self.assertEqual(1, code)
        self.assertIn("application.spec.source.helm.valueFiles", stderr)

    def test_internal_redis_rejects_competing_endpoint_or_secret_without_echoing_values(self):
        cases = (
            ("checkout", "app", "persistence", "redis", "endpoint"),
            ("checkout", "app", "persistence", "redis", "secretName"),
            ("ui", "app", "session", "redis", "secretName"),
        )
        for path in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "do-not-print-this"
                self.persist()
                code, _, stderr = self.values_check()
                self.assertEqual(1, code)
                self.assertIn(".".join(path), stderr)
                self.assertNotIn("do-not-print-this", stderr)
                target[path[-1]] = ""

    def test_internal_redis_rejects_tls_unsupported_by_the_redis_chart(self):
        self.values["checkout"]["app"]["persistence"]["redis"]["tls"] = True
        self.persist()
        code, _, stderr = self.values_check()
        self.assertEqual(1, code)
        self.assertIn("checkout.app.persistence.redis.tls", stderr)

    def test_internal_redis_requires_valid_service_port(self):
        for port in (None, True, "6379", 0, 65536):
            with self.subTest(port=port):
                self.values["checkout"]["redis"]["service"]["port"] = port
                self.persist()
                code, _, stderr = self.values_check()
                self.assertEqual(1, code)
                self.assertIn("checkout.redis.service.port", stderr)

    def test_external_redis_still_requires_an_address(self):
        self.values["checkout"]["redis"]["create"] = False
        self.persist()
        code, _, stderr = self.values_check()
        self.assertEqual(1, code)
        self.assertIn("checkout.app.persistence.redis.endpoint", stderr)

    def test_external_redis_remains_supported(self):
        self.values = self.valid_values("gcp")
        self.persist()
        code, _, stderr = self.values_check()
        self.assertEqual(0, code, stderr)

    def test_values_mode_keeps_registry_and_secret_guards(self):
        self.values["ui"]["image"]["repository"] = "wrong-registry/retail-ui"
        self.values["orders"]["app"]["persistence"]["secret"]["name"] = ""
        self.persist()
        code, _, stderr = self.values_check()
        self.assertEqual(1, code)
        self.assertIn("ui.image.repository", stderr)
        self.assertIn("orders.app.persistence.secret.name", stderr)


class AwsValuesModeTests(ValidatorTestCase):
    def test_values_mode_accepts_aws_without_changing_its_contract(self):
        code, stdout, stderr = self.invoke(mode="values", application=False)
        self.assertEqual(0, code, stderr)
        self.assertIn("Application and runtime unverified", stdout)

    def test_values_mode_does_not_allow_internal_redis_for_aws(self):
        self.values["checkout"]["redis"]["create"] = True
        self.persist()
        code, _, stderr = self.invoke(mode="values", application=False)
        self.assertEqual(1, code)
        self.assertIn("checkout.redis.create", stderr)


class ActualGcpValuesTests(unittest.TestCase):
    def test_current_gcp_dr_values_validate_without_real_application(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--environment", "gcp", "--mode", "values",
                "-f", str(REPO_ROOT / "environments/gcp/values-dr.yaml"),
                "-f", str(REPO_ROOT / "src/app/chart/versions.yaml"),
            ], repo_root=REPO_ROOT)
        self.assertEqual(0, code, stderr.getvalue())
        self.assertIn("Application and runtime unverified", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
