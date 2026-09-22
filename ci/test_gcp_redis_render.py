"""Verify the real GCP child-chart Redis wiring without a cluster or repository writes."""

import copy
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from check_rendered import check_documents
from validate_cd import ValidationErrors, load_values_stack


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("helm"), "helm is required for GCP Redis wiring tests")
class GcpRedisRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        errors = ValidationErrors()
        values, _ = load_values_stack(ROOT, [
            ROOT / "environments/gcp/values.yaml",
            ROOT / "src/app/chart/versions.yaml",
        ], errors)
        if errors.items:
            raise AssertionError("GCP values could not be loaded")
        cls.baseline = []
        # Like the AWS wiring tests, render source child charts before any
        # umbrella dependency build. The release/namespace are test-only names.
        for service in ("checkout", "ui"):
            result = subprocess.run([
                "helm", "template", "gcp-redis-test", str(ROOT / "src" / service / "chart"),
                "--namespace", "gcp-redis-test", "--skip-tests", "-f", "-",
            ], input=yaml.safe_dump(values[service]), capture_output=True,
                text=True, encoding="utf-8", timeout=30)
            if result.returncode:
                raise AssertionError(f"GCP {service} rendering failed (exit {result.returncode})")
            cls.baseline.extend(doc for doc in yaml.safe_load_all(result.stdout) if doc)

    def setUp(self):
        self.documents = copy.deepcopy(self.baseline)

    def resource(self, kind, name):
        matches = [doc for doc in self.documents
                   if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name]
        self.assertEqual(1, len(matches))
        return matches[0]

    def assert_wiring_error(self, expected):
        errors = check_documents(self.documents, environment="gcp")
        self.assertTrue(any(expected in error for error in errors), errors)

    def test_actual_gcp_values_render_shared_internal_redis(self):
        self.assertEqual([], check_documents(self.documents, environment="gcp"))
        service = self.resource("Service", "checkout-redis")
        self.assertEqual("ClusterIP", service["spec"]["type"])
        self.assertEqual(6379, service["spec"]["ports"][0]["port"])
        self.resource("Deployment", "checkout-redis")
        for name, variable in (("checkout", "RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL"),
                               ("ui", "SPRING_DATA_REDIS_URL")):
            self.assertEqual("redis://checkout-redis:6379", self.resource("ConfigMap", name)["data"][variable])

    def test_rejects_ui_using_another_redis_or_port(self):
        for endpoint in ("redis://another-redis:6379", "redis://checkout-redis:6380"):
            with self.subTest(endpoint=endpoint):
                self.resource("ConfigMap", "ui")["data"]["SPRING_DATA_REDIS_URL"] = endpoint
                self.assert_wiring_error("ui Redis")

    def test_rejects_checkout_using_another_redis(self):
        self.resource("ConfigMap", "checkout")["data"]["RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL"] = "redis://another-redis:6379"
        self.assert_wiring_error("checkout Redis")

    def test_rejects_both_consumers_using_the_same_wrong_redis(self):
        self.resource("ConfigMap", "checkout")["data"]["RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL"] = "redis://wrong:6379"
        self.resource("ConfigMap", "ui")["data"]["SPRING_DATA_REDIS_URL"] = "redis://wrong:6379"
        self.assert_wiring_error("Redis")

    def test_rejects_local_ui_session_when_internal_redis_is_used(self):
        self.resource("ConfigMap", "ui")["data"]["SPRING_SESSION_STORE_TYPE"] = "none"
        self.assert_wiring_error("ui session")

    def test_rejects_service_port_not_matching_the_connections(self):
        self.resource("Service", "checkout-redis")["spec"]["ports"][0]["port"] = 6380
        self.assert_wiring_error("Redis")

    def test_rejects_missing_redis_service_or_deployment(self):
        for kind in ("Service", "Deployment"):
            with self.subTest(kind=kind):
                self.documents = copy.deepcopy(self.baseline)
                self.documents.remove(self.resource(kind, "checkout-redis"))
                self.assert_wiring_error("internal Redis")

    def test_rejects_service_selector_that_does_not_target_redis(self):
        self.resource("Service", "checkout-redis")["spec"]["selector"]["app.kubernetes.io/component"] = "wrong"
        self.assert_wiring_error("internal Redis")

    def test_rejects_secret_or_inline_override_of_ui_redis(self):
        container = self.resource("Deployment", "ui")["spec"]["template"]["spec"]["containers"][0]
        original = copy.deepcopy(container.get("env", []))
        for override in (
            {"value": "redis://wrong:6379"},
            {"valueFrom": {"secretKeyRef": {"name": "do-not-print-this", "key": "url"}}},
        ):
            with self.subTest(override_type=next(iter(override))):
                container["env"] = original + [{"name": "SPRING_DATA_REDIS_URL", **override}]
                self.assert_wiring_error("ui Redis")
                self.assertNotIn("do-not-print-this", "\n".join(check_documents(self.documents, environment="gcp")))

    def test_rejects_missing_configmap_reference(self):
        container = self.resource("Deployment", "checkout")["spec"]["template"]["spec"]["containers"][0]
        container["envFrom"] = [{"configMapRef": {"name": "missing"}}]
        self.assert_wiring_error("checkout Redis")

    def test_rejects_prefixed_configmap_overriding_redis_and_session(self):
        container = self.resource("Deployment", "ui")["spec"]["template"]["spec"]["containers"][0]
        container["envFrom"].append({"prefix": "SPRING_", "configMapRef": {"name": "prefixed-override"}})
        self.documents.append({
            "kind": "ConfigMap", "metadata": {"name": "prefixed-override"},
            "data": {"DATA_REDIS_URL": "redis://wrong:6379", "SESSION_STORE_TYPE": "none"},
        })
        self.assert_wiring_error("ui Redis")
        self.assert_wiring_error("ui session")

    def test_accepts_valid_prefixed_configmap_reference(self):
        container = self.resource("Deployment", "ui")["spec"]["template"]["spec"]["containers"][0]
        container["envFrom"] = [{"prefix": "SPRING_", "configMapRef": {"name": "prefixed-config"}}]
        self.documents.append({
            "kind": "ConfigMap", "metadata": {"name": "prefixed-config"},
            "data": {"DATA_REDIS_URL": "redis://checkout-redis:6379", "SESSION_STORE_TYPE": "redis"},
        })
        self.assertEqual([], check_documents(self.documents, environment="gcp"))

    def test_external_redis_manifests_do_not_require_internal_resources(self):
        self.documents = [doc for doc in self.documents if doc.get("metadata", {}).get("name") != "checkout-redis"]
        self.resource("ConfigMap", "checkout")["data"]["RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL"] = "redis://external.internal:6379"
        self.resource("ConfigMap", "ui")["data"]["SPRING_DATA_REDIS_URL"] = "redis://external.internal:6379"
        self.assertEqual([], check_documents(self.documents, environment="gcp"))

    def test_gcp_shared_redis_policy_does_not_change_aws_render_checks(self):
        self.resource("ConfigMap", "ui")["data"]["SPRING_DATA_REDIS_URL"] = "redis://different:6379"
        self.assertEqual([], check_documents(self.documents, environment="aws"))


if __name__ == "__main__":
    unittest.main()
