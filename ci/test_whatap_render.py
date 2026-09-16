"""Render child charts to verify WhaTap environment variable compatibility."""

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET_NAME = "whatap-render-test-license"
SERVER_HOST = "collector.example.invalid"
REDIS_SECRET_NAME = "checkout-render-test-redis"
INSTRUMENTATION = "render-test-sdk"
INSTRUMENTATION_ANNOTATION = "instrumentation.opentelemetry.io/inject-sdk"


@unittest.skipUnless(shutil.which("helm"), "helm is required for WhaTap render tests")
class WhatapRenderTests(unittest.TestCase):
    def render_pod(self, service, *, enabled, server_host=SERVER_HOST):
        values = {
            "whatap": {
                "enabled": enabled,
                "secretName": SECRET_NAME,
                "serverHost": server_host,
                "appName": "render-test-app",
                "okind": "render-test-kind",
            },
            "metrics": {"enabled": True, "podAnnotations": {"prometheus.io/scrape": "true"}},
            "opentelemetry": {"enabled": True, "instrumentation": INSTRUMENTATION},
        }
        if service == "checkout":
            values["app"] = {
                "persistence": {
                    "provider": "redis",
                    "redis": {"secretName": REDIS_SECRET_NAME},
                }
            }
            values["redis"] = {"create": False}
        result = subprocess.run(
            [
                "helm", "template", "whatap-render-test",
                str(REPO_ROOT / "src" / service / "chart"),
                "--show-only", "templates/deployment.yaml", "--values", "-",
            ],
            input=yaml.safe_dump(values),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        documents = [document for document in yaml.safe_load_all(result.stdout) if document]
        self.assertEqual(1, len(documents))
        self.assertEqual("Deployment", documents[0]["kind"])
        pod = documents[0]["spec"]["template"]
        self.assertEqual("true", pod["metadata"]["annotations"]["prometheus.io/scrape"])
        return pod

    @staticmethod
    def container_env(pod):
        container = pod["spec"]["containers"][0]
        return {entry["name"]: entry for entry in container.get("env", [])}

    def assert_secret_env(self, env, name, secret_name, key):
        self.assertEqual(
            {"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}},
            env[name],
        )

    def assert_monitoring_enabled(self, pod, service):
        env = self.container_env(pod)
        self.assert_secret_env(env, "WHATAP_LICENSE", SECRET_NAME, "license")
        self.assertEqual({"name": "OTEL_ENABLED", "value": "false"}, env["OTEL_ENABLED"])
        self.assertEqual({"name": "WHATAP_HOME", "value": "/whatap"}, env["WHATAP_HOME"])
        self.assertEqual("render-test-kind", env["WHATAP_OKIND"]["value"])
        self.assertNotIn(INSTRUMENTATION_ANNOTATION, pod["metadata"]["annotations"])
        volume_name = "whatap-home" if service == "catalog" else "whatap-volume"
        self.assertIn({"name": volume_name, "emptyDir": {}}, pod["spec"]["volumes"])
        self.assertIn(
            {"name": volume_name, "mountPath": "/whatap"},
            pod["spec"]["containers"][0]["volumeMounts"],
        )
        if service == "checkout":
            self.assert_secret_env(
                env, "RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL", REDIS_SECRET_NAME, "url"
            )
            self.assertEqual("true", env["WHATAP_MICRO_ENABLED"]["value"])
            for name, field_path in (
                ("NODE_IP", "status.hostIP"),
                ("NODE_NAME", "spec.nodeName"),
                ("POD_NAME", "metadata.name"),
            ):
                self.assertEqual(
                    {"name": name, "valueFrom": {"fieldRef": {"fieldPath": field_path}}},
                    env[name],
                )

    def test_catalog_uses_configured_host_as_plain_value(self):
        pod = self.render_pod("catalog", enabled=True)
        env = self.container_env(pod)
        self.assertEqual(
            {"name": "WHATAP_SERVER_HOST", "value": SERVER_HOST}, env["WHATAP_SERVER_HOST"]
        )
        self.assertEqual("render-test-app", env["WHATAP_APP_NAME"]["value"])
        self.assert_monitoring_enabled(pod, "catalog")

    def test_checkout_uses_configured_host_as_plain_value(self):
        pod = self.render_pod("checkout", enabled=True)
        self.assertEqual(
            {"name": "WHATAP_SERVER_HOST", "value": SERVER_HOST},
            self.container_env(pod)["WHATAP_SERVER_HOST"],
        )
        self.assert_monitoring_enabled(pod, "checkout")

    def test_checkout_empty_host_retains_secret_fallback(self):
        pod = self.render_pod("checkout", enabled=True, server_host="")
        self.assert_secret_env(
            self.container_env(pod), "WHATAP_SERVER_HOST", SECRET_NAME, "serverHost"
        )
        self.assert_monitoring_enabled(pod, "checkout")

    def test_disabled_whatap_omits_its_env_and_volume(self):
        for service in ("catalog", "checkout"):
            with self.subTest(service=service):
                pod = self.render_pod(service, enabled=False)
                env = self.container_env(pod)
                self.assertFalse(any(name.startswith("WHATAP_") for name in env))
                self.assertNotIn("OTEL_ENABLED", env)
                self.assertFalse({"NODE_IP", "NODE_NAME", "POD_NAME"}.intersection(env))
                self.assertEqual(
                    INSTRUMENTATION, pod["metadata"]["annotations"][INSTRUMENTATION_ANNOTATION]
                )
                self.assertFalse(
                    any(volume["name"].startswith("whatap-") for volume in pod["spec"]["volumes"])
                )
                self.assertFalse(
                    any(
                        mount["mountPath"] == "/whatap"
                        for mount in pod["spec"]["containers"][0]["volumeMounts"]
                    )
                )
                if service == "checkout":
                    self.assert_secret_env(
                        env, "RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL", REDIS_SECRET_NAME, "url"
                    )


if __name__ == "__main__":
    unittest.main()
