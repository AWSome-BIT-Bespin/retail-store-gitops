"""Check the approved AWS identity/Secret wiring without accessing a cluster."""

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from validate_cd import ValidationErrors, load_values_stack


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("helm"), "helm is required for AWS wiring tests")
class AwsSecretIdentityRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application = yaml.safe_load((ROOT / "applications/aws.yaml").read_text(encoding="utf-8"))
        source = application["spec"]["source"]
        cls.namespace = application["spec"]["destination"]["namespace"]
        chart = ROOT / source["path"]
        paths = [(chart / path).resolve() for path in source["helm"]["valueFiles"]]
        errors = ValidationErrors()
        values, _ = load_values_stack(ROOT, paths, errors)
        if errors.items:
            raise AssertionError("AWS values could not be loaded: " + "; ".join(errors.items))

        cls.documents = {}
        # These tests run BEFORE umbrella dependency build in helm-ci.yml.
        # Render source child charts with the actual coalesced AWS values, so a
        # fresh checkout needs neither vendored archives nor a repository write.
        for service in ("cart", "checkout", "orders", "ui"):
            result = subprocess.run(
                [
                    "helm", "template", source["helm"]["releaseName"],
                    str(ROOT / "src" / service / "chart"),
                    "--namespace", cls.namespace, "--skip-tests", "-f", "-",
                ],
                input=yaml.safe_dump(values[service]),
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            if result.returncode:
                # Do not echo values or rendered Secret contents into CI logs.
                raise AssertionError(f"AWS {service} chart rendering failed (exit {result.returncode})")
            cls.documents[service] = [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def resource(self, service, kind):
        matches = [doc for doc in self.documents[service] if doc.get("kind") == kind]
        self.assertEqual(1, len(matches), f"Expected one {kind} in {service}")
        return matches[0]

    def container(self, service):
        containers = self.resource(service, "Deployment")["spec"]["template"]["spec"]["containers"]
        self.assertEqual(1, len(containers), f"Expected one application container in {service}")
        return containers[0]

    def assert_redis_secret(self, service, variable):
        entries = [entry for entry in self.container(service).get("env", []) if entry.get("name") == variable]
        self.assertEqual(1, len(entries), f"Expected one {variable} reference")
        self.assertNotIn("value", entries[0], "Redis URL must not be an inline value")
        reference = entries[0].get("valueFrom", {}).get("secretKeyRef", {})
        self.assertEqual("retail-redis", reference.get("name"))
        self.assertEqual("url", reference.get("key"))
        self.assertIsNot(reference.get("optional"), True, "Redis Secret must be required")
        self.assertNotIn(variable, self.resource(service, "ConfigMap").get("data", {}))

    def test_application_namespace_matches_aws_identity_and_secret_contract(self):
        self.assertEqual("retail-store", self.namespace)
        for service, documents in self.documents.items():
            for document in documents:
                namespace = document.get("metadata", {}).get("namespace")
                self.assertIn(namespace, (None, "retail-store"), f"Unexpected namespace in {service}")

    def test_cart_deployment_uses_pod_identity_service_account_and_table(self):
        account = self.resource("cart", "ServiceAccount")
        self.assertEqual("carts-dynamo-sa", account["metadata"]["name"])
        self.assertNotIn("eks.amazonaws.com/role-arn", account["metadata"].get("annotations", {}))
        pod_spec = self.resource("cart", "Deployment")["spec"]["template"]["spec"]
        self.assertEqual("carts-dynamo-sa", pod_spec["serviceAccountName"])
        config = self.resource("cart", "ConfigMap")["data"]
        self.assertEqual("dynamodb", config["RETAIL_CART_PERSISTENCE_PROVIDER"])
        self.assertEqual("retail-store-cart", config["RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME"])
        env_names = {entry["name"] for entry in self.container("cart").get("env", [])}
        self.assertNotIn("RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME", env_names)

    def test_checkout_reads_redis_url_from_required_secret(self):
        self.assert_redis_secret("checkout", "RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL")

    def test_ui_reads_redis_url_from_required_secret(self):
        self.assert_redis_secret("ui", "SPRING_DATA_REDIS_URL")
        self.assertEqual("redis", self.resource("ui", "ConfigMap")["data"]["SPRING_SESSION_STORE_TYPE"])

    def test_orders_reads_external_secret_without_overriding_its_endpoint(self):
        container = self.container("orders")
        refs = [item["secretRef"] for item in container.get("envFrom", []) if "secretRef" in item]
        self.assertEqual(1, len(refs))
        self.assertEqual("orders-db", refs[0].get("name"))
        self.assertIsNot(refs[0].get("optional"), True, "Orders Secret must be required")
        config = self.resource("orders", "ConfigMap")["data"]
        self.assertEqual("postgres", config["RETAIL_ORDERS_PERSISTENCE_PROVIDER"])
        # envFrom consumes these keys from ESO's orders-db. Rendering cannot
        # prove they exist in the live Secret, only that we don't override them.
        env_names = {entry["name"] for entry in container.get("env", [])}
        for key in (
            "RETAIL_ORDERS_PERSISTENCE_USERNAME",
            "RETAIL_ORDERS_PERSISTENCE_PASSWORD",
            "RETAIL_ORDERS_PERSISTENCE_ENDPOINT",
        ):
            self.assertNotIn(key, config)
            self.assertNotIn(key, env_names)
        self.assertFalse(any(
            doc.get("kind") == "Secret" and doc.get("metadata", {}).get("name") == "orders-db"
            for doc in self.documents["orders"]
        ), "Orders must reference the external Secret, not create it")


if __name__ == "__main__":
    unittest.main()
