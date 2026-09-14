import copy
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml


CI_DIR = Path(__file__).resolve().parent
REPO_ROOT = CI_DIR.parent
sys.path.insert(0, str(CI_DIR))

from validate_cd import main  # noqa: E402


SERVICES = ("cart", "catalog", "checkout", "orders", "ui")
AWS_REGISTRY = "350606136784.dkr.ecr.ap-northeast-2.amazonaws.com"
GCP_REGISTRY = "asia-northeast3-docker.pkg.dev/kdt4-3/retail-store"


class ValidatorTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        for service in SERVICES:
            chart = self.root / "src" / service / "chart"
            chart.mkdir(parents=True)
            self.write_yaml(
                chart / "values.yaml",
                {
                    "image": {"repository": f"{AWS_REGISTRY}/retail-{service}", "tag": None},
                    "serviceAccount": {"create": True, "annotations": {}},
                    "podAnnotations": {},
                    "service": {"annotations": {}},
                },
            )
        app_chart = self.root / "src" / "app" / "chart"
        app_chart.mkdir(parents=True)
        self.write_yaml(
            app_chart / "values.yaml",
            {
                "cart": {
                    "app": {
                        "persistence": {
                            "provider": "in-memory",
                            "dynamodb": {"tableName": "Items", "createTable": False},
                        }
                    },
                    "dynamodb": {"create": False},
                },
                "catalog": {
                    "app": {
                        "persistence": {"provider": "in-memory"},
                        "search": {"enabled": False, "provider": "self-hosted"},
                    },
                    "whatap": {"enabled": False, "secretName": "", "serverHost": ""},
                },
                "checkout": {
                    "app": {
                        "persistence": {
                            "provider": "in-memory",
                            "redis": {"endpoint": "", "tls": False},
                        }
                    },
                    "redis": {"create": False},
                    "whatap": {"enabled": False, "secretName": "", "serverHost": ""},
                },
                "orders": {
                    "app": {
                        "persistence": {
                            "provider": "in-memory",
                            "endpoint": "",
                            "database": "orders",
                            "secret": {"create": True, "name": "orders-db"},
                        },
                        "messaging": {"provider": "in-memory"},
                    },
                    "postgresql": {"create": False},
                },
                "ui": {
                    "app": {
                        "session": {"redis": {"endpoint": "", "tls": False}},
                        "chat": {"enabled": False, "provider": ""},
                    },
                    "ingress": {
                        "enabled": True,
                        "className": "alb",
                        "annotations": {
                            "alb.ingress.kubernetes.io/scheme": "internet-facing"
                        },
                    },
                },
            },
        )
        self.environment = "aws"
        self.values_path = self.root / "environments" / "aws" / "values.yaml"
        self.versions_path = app_chart / "versions.yaml"
        self.application_path = self.root / "applications" / "aws.yaml"
        self.values = self.valid_values("aws")
        self.versions = {
            service: {"image": {"tag": f"v1.2.{index}"}}
            for index, service in enumerate(SERVICES, start=1)
        }
        self.application = self.valid_application("aws")
        self.persist()

    def tearDown(self):
        self._temporary.cleanup()

    @staticmethod
    def write_yaml(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    def valid_values(self, environment):
        registry = AWS_REGISTRY if environment == "aws" else GCP_REGISTRY
        cart_provider = "dynamodb" if environment == "aws" else "in-memory"
        cart_annotations = (
            {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/retail-cart"}
            if environment == "aws"
            else {}
        )
        ingress = (
            {
                "enabled": True,
                "className": "alb",
                "annotations": {
                    "alb.ingress.kubernetes.io/scheme": "internet-facing"
                },
            }
            if environment == "aws"
            else {
                "enabled": False,
                "className": "",
                "annotations": {"alb.ingress.kubernetes.io/scheme": None},
            }
        )
        return {
            "cart": {
                "image": {"repository": f"{registry}/retail-cart"},
                "serviceAccount": {"annotations": cart_annotations},
                "app": {
                    "persistence": {
                        "provider": cart_provider,
                        "dynamodb": {"tableName": "retail-cart", "createTable": False},
                    }
                },
                "dynamodb": {"create": False},
            },
            "catalog": {
                "image": {"repository": f"{registry}/retail-catalog"},
                "app": {"persistence": {"provider": "in-memory"}},
                "whatap": {"enabled": False, "secretName": "", "serverHost": ""},
            },
            "checkout": {
                "image": {"repository": f"{registry}/retail-checkout"},
                "app": {
                    "persistence": {
                        "provider": "redis",
                        "redis": {"endpoint": "redis.internal:6379", "tls": True},
                    }
                },
                "redis": {"create": False},
                "whatap": {"enabled": False, "secretName": "", "serverHost": ""},
            },
            "orders": {
                "image": {"repository": f"{registry}/retail-orders"},
                "app": {
                    "persistence": {
                        "provider": "postgres",
                        "endpoint": "postgres.internal:5432",
                        "database": "orders",
                        "secret": {"create": False, "name": "orders-db"},
                    },
                    "messaging": {"provider": "in-memory"},
                },
                "postgresql": {"create": False},
                "securityGroups": {"create": False, "securityGroupIds": []},
            },
            "ui": {
                "image": {"repository": f"{registry}/retail-ui"},
                "ingress": ingress,
                "app": {
                    "endpoints": {},
                    "session": {
                        "redis": {"endpoint": "redis.internal:6379", "tls": False}
                    }
                },
            },
        }

    @staticmethod
    def valid_application(environment):
        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": f"retail-{environment}", "namespace": "argocd"},
            "spec": {
                "project": "retail",
                "source": {
                    "repoURL": "https://github.com/AWSome-BIT-Bespin/retail-store-gitops.git",
                    "targetRevision": "main",
                    "path": "src/app/chart",
                    "helm": {
                        "releaseName": "retail-store",
                        "valueFiles": [
                            f"../../../environments/{environment}/values.yaml",
                            "versions.yaml",
                        ],
                    },
                },
                "destination": {"name": "in-cluster", "namespace": "retail"},
            },
        }

    def persist(self):
        self.write_yaml(self.values_path, self.values)
        self.write_yaml(self.versions_path, self.versions)
        self.write_yaml(self.application_path, self.application)

    def invoke(self, *, mode="deployment", environment=None, files=None, application=True):
        environment = environment or self.environment
        files = files or [self.values_path, self.versions_path]
        args = ["--environment", environment, "--mode", mode]
        for path in files:
            args.extend(["-f", str(path)])
        if application:
            args.extend(["--application", str(self.application_path)])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args, repo_root=self.root)
        return code, stdout.getvalue(), stderr.getvalue()


class PositiveValidationTests(ValidatorTestCase):
    def test_accepts_complete_aws_deployment_inputs(self):
        code, stdout, stderr = self.invoke()

        self.assertEqual(0, code, stderr)
        self.assertIn("deployment input validation succeeded", stdout)
        self.assertIn("runtime unverified", stdout)
    def test_accepts_complete_gcp_deployment_inputs(self):
        self.environment = "gcp"
        self.values_path = self.root / "environments" / "gcp" / "values.yaml"
        self.application_path = self.root / "applications" / "gcp.yaml"
        self.values = self.valid_values("gcp")
        self.application = self.valid_application("gcp")
        self.persist()

        code, _, stderr = self.invoke(environment="gcp")

        self.assertEqual(0, code, stderr)

    def test_structure_mode_allows_non_live_placeholders_and_needs_no_application(self):
        self.values["checkout"]["app"]["persistence"]["redis"]["endpoint"] = "redis.example.invalid:6379"
        self.values["orders"]["app"]["persistence"]["endpoint"] = "REQUIRED"
        self.persist()

        code, stdout, stderr = self.invoke(mode="structure", application=False)

        self.assertEqual(0, code, stderr)
        self.assertIn("structural validation succeeded", stdout)
        self.assertIn("runtime unverified", stdout)


class ActualStructureStackTests(unittest.TestCase):
    def test_actual_chart_defaults_and_structure_fixtures_validate_for_both_clouds(self):
        for environment in ("aws", "gcp"):
            with self.subTest(environment=environment):
                args = [
                    "--environment",
                    environment,
                    "--mode",
                    "structure",
                    "-f",
                    str(REPO_ROOT / "environments" / environment / "values.example.yaml"),
                    "-f",
                    str(REPO_ROOT / "ci" / "fixtures" / f"{environment}.yaml"),
                    "-f",
                    str(REPO_ROOT / "src" / "app" / "chart" / "versions.yaml"),
                ]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(args, repo_root=REPO_ROOT)
                self.assertEqual(0, code, stderr.getvalue())

    def test_actual_gcp_stack_rejects_aws_security_group_policy_settings(self):
        cases = [
            {"orders": {"securityGroups": {"create": True}}},
            {
                "orders": {
                    "securityGroups": {
                        "securityGroupIds": ["sg-0123456789abcdef0"]
                    }
                }
            },
        ]
        for overlay in cases:
            with self.subTest(overlay=overlay), tempfile.TemporaryDirectory() as directory:
                overlay_path = Path(directory) / "gcp-security-groups.yaml"
                ValidatorTestCase.write_yaml(overlay_path, overlay)
                args = [
                    "--environment",
                    "gcp",
                    "--mode",
                    "structure",
                    "-f",
                    str(REPO_ROOT / "environments" / "gcp" / "values.example.yaml"),
                    "-f",
                    str(REPO_ROOT / "ci" / "fixtures" / "gcp.yaml"),
                    "-f",
                    str(overlay_path),
                    "-f",
                    str(REPO_ROOT / "src" / "app" / "chart" / "versions.yaml"),
                ]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(args, repo_root=REPO_ROOT)
                self.assertEqual(1, code)
                self.assertIn("orders.securityGroups", stderr.getvalue())
                self.assertIn("GCP", stderr.getvalue())


class YamlAndTypeValidationTests(ValidatorTestCase):
    def test_rejects_duplicate_yaml_keys(self):
        self.values_path.write_text("cart: {}\ncart: {}\n", encoding="utf-8")

        code, _, stderr = self.invoke(mode="structure", application=False)

        self.assertEqual(1, code)
        self.assertIn("duplicate key", stderr)
        self.assertIn(str(self.values_path), stderr)

    def test_rejects_malformed_yaml_and_non_mapping_root(self):
        for content, expected in [("cart: [\n", "invalid YAML"), ("- cart\n", "root must be a mapping")]:
            with self.subTest(content=content):
                self.values_path.write_text(content, encoding="utf-8")
                code, _, stderr = self.invoke(mode="structure", application=False)
                self.assertEqual(1, code)
                self.assertIn(expected, stderr)

    def test_reports_missing_input_file(self):
        missing = self.root / "missing.yaml"

        code, _, stderr = self.invoke(mode="structure", files=[missing], application=False)

        self.assertEqual(1, code)
        self.assertIn(str(missing), stderr)
        self.assertIn("not found", stderr)

    def test_rejects_non_utf8_yaml_without_a_traceback(self):
        self.values_path.write_bytes(b"cart:\n  value: \xff\n")

        code, _, stderr = self.invoke(mode="structure", application=False)

        self.assertEqual(1, code)
        self.assertIn("could not be decoded as UTF-8", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_rejects_original_array_types_with_field_paths(self):
        cases = [
            (("cart", "serviceAccount", "annotations"), []),
            (("checkout", "app", "persistence", "redis", "endpoint"), []),
            (("ui", "app", "session", "redis", "endpoint"), []),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke(mode="structure", application=False)
                self.assertEqual(1, code)
                self.assertIn(".".join(path), stderr)
                self.values = self.valid_values("aws")

    def test_rejects_non_string_annotation_values_without_echoing_them(self):
        secret_value = "DO-NOT-PRINT-THIS-VALUE"
        self.values["cart"]["serviceAccount"]["annotations"] = {"example/key": [secret_value]}
        self.persist()

        code, _, stderr = self.invoke(mode="structure", application=False)

        self.assertEqual(1, code)
        self.assertIn("cart.serviceAccount.annotations.example/key", stderr)
        self.assertNotIn(secret_value, stderr)

    def test_null_overlay_deletes_inherited_annotation(self):
        self.environment = "gcp"
        self.values_path = self.root / "environments" / "gcp" / "values.yaml"
        self.application_path = self.root / "applications" / "gcp.yaml"
        self.values = self.valid_values("gcp")
        self.values["ui"]["ingress"]["annotations"] = {
            "alb.ingress.kubernetes.io/scheme": None
        }
        self.values["cart"]["serviceAccount"]["annotations"] = {
            "eks.amazonaws.com/role-arn": None
        }
        self.application = self.valid_application("gcp")
        self.persist()

        code, _, stderr = self.invoke(mode="structure", environment="gcp", application=False)

        self.assertEqual(0, code, stderr)


class ImageValidationTests(ValidatorTestCase):
    def test_structure_requires_all_tags_in_a_supplied_versions_file(self):
        del self.versions["orders"]
        self.persist()

        code, _, stderr = self.invoke(mode="structure", application=False)

        self.assertEqual(1, code)
        self.assertIn("orders.image.tag", stderr)

    def test_rejects_mutable_or_missing_service_tags(self):
        for bad_tag in ("latest", "v1.2", "main", "v01.2.3", "v١.2.3", None):
            with self.subTest(tag=bad_tag):
                self.versions["catalog"]["image"]["tag"] = bad_tag
                self.persist()
                code, _, stderr = self.invoke(mode="deployment")
                self.assertEqual(1, code)
                self.assertIn("catalog.image.tag", stderr)
                self.versions["catalog"]["image"]["tag"] = "v1.2.2"

    def test_rejects_wrong_registry_in_both_modes(self):
        self.values["ui"]["image"]["repository"] = f"{GCP_REGISTRY}/retail-ui"
        self.persist()

        for mode in ("structure", "deployment"):
            with self.subTest(mode=mode):
                code, _, stderr = self.invoke(mode=mode, application=(mode == "deployment"))
                self.assertEqual(1, code)
                self.assertIn("ui.image.repository", stderr)


class DeploymentValueValidationTests(ValidatorTestCase):
    def test_deployment_requires_application(self):
        code, _, stderr = self.invoke(application=False)

        self.assertEqual(1, code)
        self.assertIn("--application", stderr)

    def test_rejects_blank_fake_and_secret_like_required_values_without_leaking_values(self):
        cases = [
            (("checkout", "app", "persistence", "redis", "endpoint"), "redis.example.invalid:6379"),
            (("orders", "app", "persistence", "endpoint"), "CHANGEME-super-secret"),
            (("orders", "app", "persistence", "secret", "name"), ""),
            (("ui", "app", "session", "redis", "endpoint"), "REQUIRED"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn(".".join(path), stderr)
                if value:
                    self.assertNotIn(value, stderr)
                self.values = self.valid_values("aws")

    def test_requires_external_host_port_endpoints(self):
        cases = [
            (("checkout", "app", "persistence", "redis", "endpoint"), "redis.internal"),
            (("orders", "app", "persistence", "endpoint"), "postgres.internal"),
            (("ui", "app", "session", "redis", "endpoint"), "https://redis.internal:6379"),
            (("checkout", "app", "persistence", "redis", "endpoint"), " redis.internal:6379"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn("host:port", stderr)
                self.values = self.valid_values("aws")

    def test_rejects_unsupported_providers(self):
        cases = [
            (("cart", "app", "persistence", "provider"), "spanner"),
            (("catalog", "app", "persistence", "provider"), "postgres"),
            (("checkout", "app", "persistence", "provider"), "memcached"),
            (("orders", "app", "messaging", "provider"), "kafka"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn(".".join(path), stderr)
                self.values = self.valid_values("aws")

    def test_gcp_requires_explicit_supported_cart_provider(self):
        self.environment = "gcp"
        self.values_path = self.root / "environments" / "gcp" / "values.yaml"
        self.application_path = self.root / "applications" / "gcp.yaml"
        self.values = self.valid_values("gcp")
        self.values["cart"]["app"]["persistence"]["provider"] = ""
        self.application = self.valid_application("gcp")
        self.persist()

        code, _, stderr = self.invoke(environment="gcp")

        self.assertEqual(1, code)
        self.assertIn("cart.app.persistence.provider", stderr)

    def test_gcp_dynamodb_requires_a_separately_reviewed_design(self):
        self.environment = "gcp"
        self.values_path = self.root / "environments" / "gcp" / "values.yaml"
        self.application_path = self.root / "applications" / "gcp.yaml"
        self.values = self.valid_values("gcp")
        self.values["cart"]["app"]["persistence"]["provider"] = "dynamodb"
        self.application = self.valid_application("gcp")
        self.persist()

        code, _, stderr = self.invoke(environment="gcp")

        self.assertEqual(1, code)
        self.assertIn("separately reviewed data/identity design", stderr)

    def test_aws_dynamodb_requires_table_and_irsa_role_arn(self):
        cases = [
            (("cart", "app", "persistence", "dynamodb", "tableName"), ""),
            (("cart", "app", "persistence", "dynamodb", "createTable"), True),
            (("cart", "dynamodb", "create"), True),
            (("cart", "serviceAccount", "create"), False),
            (("cart", "serviceAccount", "annotations", "eks.amazonaws.com/role-arn"), "not-an-arn"),
            (("cart", "serviceAccount", "annotations", "eks.amazonaws.com/role-arn"), " arn:aws:iam::123456789012:role/cart"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn(".".join(path), stderr)
                self.values = self.valid_values("aws")

    def test_orders_external_postgres_requires_existing_secret(self):
        self.values["orders"]["app"]["persistence"]["secret"]["create"] = True
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("orders.app.persistence.secret.create", stderr)

    def test_whatap_requires_secret_and_server_when_enabled(self):
        for service in ("catalog", "checkout"):
            with self.subTest(service=service):
                self.values[service]["whatap"] = {
                    "enabled": True,
                    "secretName": "",
                    "serverHost": "",
                }
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn(f"{service}.whatap.secretName", stderr)
                self.assertIn(f"{service}.whatap.serverHost", stderr)
                self.values = self.valid_values("aws")

    def test_rejects_ui_redis_tls_until_chart_supports_it(self):
        self.values["ui"]["app"]["session"]["redis"]["tls"] = True
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("ui.app.session.redis.tls", stderr)
        self.assertIn("unsupported", stderr)

    def test_rejects_aws_specific_settings_in_gcp_structure_and_deployment(self):
        self.environment = "gcp"
        self.values_path = self.root / "environments" / "gcp" / "values.yaml"
        self.application_path = self.root / "applications" / "gcp.yaml"
        self.values = self.valid_values("gcp")
        self.application = self.valid_application("gcp")
        cases = [
            (("cart", "serviceAccount", "annotations", "eks.amazonaws.com/role-arn"), "arn:aws:iam::123456789012:role/cart"),
            (("ui", "ingress", "annotations", "alb.ingress.kubernetes.io/scheme"), "internet-facing"),
            (("ui", "ingress", "className"), "alb"),
            (("ui", "app", "endpoints", "catalog"), "https://catalog.amazonaws.com"),
            (("orders", "securityGroups", "create"), True),
            (("orders", "securityGroups", "securityGroupIds"), ["sg-0123456789abcdef0"]),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.values
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                for mode in ("structure", "deployment"):
                    code, _, stderr = self.invoke(
                        mode=mode, environment="gcp", application=(mode == "deployment")
                    )
                    self.assertEqual(1, code)
                    self.assertIn("GCP", stderr)
                self.values = self.valid_values("gcp")

    def test_refuses_example_and_fixture_paths_for_deployment(self):
        for path in (
            self.root / "environments" / "aws" / "values.example.yaml",
            self.root / "ci" / "fixtures" / "aws.yaml",
        ):
            with self.subTest(path=path):
                self.write_yaml(path, self.values)
                code, _, stderr = self.invoke(files=[path, self.versions_path])
                self.assertEqual(1, code)
                self.assertIn("deployment input", stderr)

    def test_refuses_example_application_path_for_deployment(self):
        self.application_path = self.root / "applications" / "aws.example.yaml"
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("application", stderr)
        self.assertIn("deployment input", stderr)


class ApplicationValidationTests(ValidatorTestCase):
    def test_requires_promoted_application_path(self):
        self.application_path = self.root / "other" / "aws.yaml"
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("--application", stderr)

    def test_rejects_wrong_application_identity_and_source_fields(self):
        cases = [
            (("apiVersion",), "v1"),
            (("kind",), "Deployment"),
            (("metadata", "name"), ""),
            (("metadata", "namespace"), "CHANGEME"),
            (("spec", "project"), ""),
            (("spec", "source", "repoURL"), "https://example.invalid/repo.git"),
            (("spec", "source", "targetRevision"), ""),
            (("spec", "source", "path"), "chart"),
            (("spec", "source", "helm", "releaseName"), ""),
            (("spec", "destination", "namespace"), ""),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.application
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn("application." + ".".join(path), stderr)
                self.application = self.valid_application("aws")

    def test_requires_exact_value_file_order(self):
        self.application["spec"]["source"]["helm"]["valueFiles"].reverse()
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("application.spec.source.helm.valueFiles", stderr)

    def test_rejects_input_stack_that_differs_from_application_stack(self):
        alternate = self.root / "environments" / "aws" / "alternate.yaml"
        self.write_yaml(alternate, self.values)

        code, _, stderr = self.invoke(files=[alternate, self.versions_path])

        self.assertEqual(1, code)
        self.assertIn("-f input stack", stderr)

    def test_requires_exactly_one_destination_selector(self):
        self.application["spec"]["destination"]["server"] = "https://kubernetes.default.svc"
        self.persist()
        code, _, stderr = self.invoke()
        self.assertEqual(1, code)
        self.assertIn("application.spec.destination", stderr)

        del self.application["spec"]["destination"]["name"]
        del self.application["spec"]["destination"]["server"]
        self.persist()
        code, _, stderr = self.invoke()
        self.assertEqual(1, code)
        self.assertIn("application.spec.destination", stderr)

    def test_rejects_multi_source_and_unsafe_helm_features(self):
        cases = [
            (("spec", "sources"), []),
            (("spec", "source", "helm", "values"), "cart: {}"),
            (("spec", "source", "helm", "valuesObject"), {"cart": {}}),
            (("spec", "source", "helm", "parameters"), []),
            (("spec", "source", "helm", "fileParameters"), []),
            (("spec", "source", "helm", "ignoreMissingValueFiles"), True),
            (("spec", "source", "helm", "skipSchemaValidation"), True),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                target = self.application
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn("application." + ".".join(path), stderr)
                self.application = self.valid_application("aws")

    def test_rejects_automated_sync_finalizer_force_and_replace(self):
        mutations = [
            lambda app: app["spec"].update({"syncPolicy": {"automated": {}}}),
            lambda app: app["metadata"].update({"finalizers": ["resources-finalizer.argocd.argoproj.io"]}),
            lambda app: app["spec"].update({"syncPolicy": {"syncOptions": ["Force=true"]}}),
            lambda app: app["spec"].update({"syncPolicy": {"syncOptions": ["Replace=true"]}}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                mutate(self.application)
                self.persist()
                code, _, stderr = self.invoke()
                self.assertEqual(1, code)
                self.assertIn("application.", stderr)
                self.application = self.valid_application("aws")

    def test_rejects_top_level_operation_sync_request(self):
        self.application["operation"] = {
            "sync": {"prune": True, "syncStrategy": {"apply": {"force": True}}}
        }
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(1, code)
        self.assertIn("application.operation", stderr)

    def test_allows_explicitly_disabled_automated_sync(self):
        self.application["spec"]["syncPolicy"] = {"automated": {"enabled": False}}
        self.persist()

        code, _, stderr = self.invoke()

        self.assertEqual(0, code, stderr)


class HelmSchemaTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("helm"), "helm is required for schema integration test")
    def test_schema_rejects_legacy_array_annotations_and_redis_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_values = Path(directory) / "bad.yaml"
            bad_values.write_text(
                "cart:\n  serviceAccount:\n    annotations: []\n"
                "ui:\n  app:\n    session:\n      redis:\n        endpoint: []\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["helm", "lint", str(REPO_ROOT / "src" / "app" / "chart"), "-f", str(bad_values)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        output = result.stdout + result.stderr
        self.assertIn("/cart/serviceAccount/annotations", output)
        self.assertIn("/ui/app/session/redis/endpoint", output)

    @unittest.skipUnless(shutil.which("helm"), "helm is required for schema integration test")
    def test_schema_rejects_nested_service_annotation_arrays(self):
        cases = [
            (
                "checkout:\n  redis:\n    service:\n      annotations: []\n",
                "/checkout/redis/service/annotations",
            ),
            (
                "ui:\n  ingresses:\n    - annotations: []\n",
                "/ui/ingresses/0/annotations",
            ),
        ]
        for content, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                bad_values = Path(directory) / "bad.yaml"
                bad_values.write_text(content, encoding="utf-8")
                result = subprocess.run(
                    ["helm", "lint", str(REPO_ROOT / "src" / "app" / "chart"), "-f", str(bad_values)],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn(expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
