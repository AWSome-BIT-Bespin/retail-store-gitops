#!/usr/bin/env python3
"""Validate retail-store Helm and Argo CD deployment inputs offline."""

from __future__ import annotations

import argparse
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import yaml
from yaml.constructor import ConstructorError


SERVICES = ("cart", "catalog", "checkout", "orders", "ui")
REPOSITORY = "https://github.com/AWSome-BIT-Bespin/retail-store-gitops.git"
REGISTRIES = {
    "aws": "350606136784.dkr.ecr.ap-northeast-2.amazonaws.com",
    "gcp": "asia-northeast3-docker.pkg.dev/kdt4-3/retail-store",
}
PROVIDERS = {
    ("cart", "app", "persistence", "provider"): {"in-memory", "dynamodb"},
    ("catalog", "app", "persistence", "provider"): {"in-memory", "mysql"},
    ("catalog", "app", "search", "provider"): {"self-hosted"},
    ("checkout", "app", "persistence", "provider"): {"in-memory", "redis"},
    ("orders", "app", "persistence", "provider"): {"in-memory", "postgres"},
    ("orders", "app", "messaging", "provider"): {"in-memory", "rabbitmq"},
    ("ui", "app", "chat", "provider"): {"", "openai", "bedrock"},
}
BOOLEAN_PATHS = {
    *((service, section, key) for service in SERVICES for section, key in (
        ("serviceAccount", "create"),
        ("autoscaling", "enabled"),
        ("metrics", "enabled"),
        ("configMap", "create"),
        ("opentelemetry", "enabled"),
        ("podDisruptionBudget", "enabled"),
    )),
    ("cart", "app", "persistence", "dynamodb", "createTable"),
    ("cart", "dynamodb", "create"),
    ("catalog", "app", "persistence", "secret", "create"),
    ("catalog", "app", "search", "enabled"),
    ("catalog", "mysql", "create"),
    ("catalog", "mysql", "persistentVolume", "enabled"),
    ("catalog", "opensearch", "persistentVolume", "enabled"),
    ("catalog", "securityGroups", "create"),
    ("catalog", "whatap", "enabled"),
    ("checkout", "app", "persistence", "redis", "tls"),
    ("checkout", "redis", "create"),
    ("checkout", "securityGroups", "create"),
    ("checkout", "whatap", "enabled"),
    ("orders", "app", "persistence", "secret", "create"),
    ("orders", "app", "messaging", "rabbitmq", "secret", "create"),
    ("orders", "postgresql", "create"),
    ("orders", "postgresql", "persistentVolume", "enabled"),
    ("orders", "rabbitmq", "create"),
    ("orders", "rabbitmq", "persistentVolume", "enabled"),
    ("orders", "securityGroups", "create"),
    ("ui", "app", "session", "redis", "tls"),
    ("ui", "app", "search", "enabled"),
    ("ui", "app", "chat", "enabled"),
    ("ui", "ingress", "enabled"),
    ("ui", "istio", "enabled"),
}
IMMUTABLE_TAG = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
HOST_PORT = re.compile(r"^(?:\[[0-9A-Fa-f:]+\]|[A-Za-z0-9][A-Za-z0-9._-]*):(?:[1-9]\d{0,4})$")
IRSA_ROLE = re.compile(r"^arn:aws:iam::(\d{12}):role/[A-Za-z0-9+=,.@_/-]+$")
MISSING = object()


class DuplicateKeyError(Exception):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping keys must be scalar",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise DuplicateKeyError(f"duplicate key at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class ValidationErrors:
    def __init__(self):
        self.items: list[str] = []

    def add(self, path: str, message: str):
        self.items.append(f"{path}: {message}")

    def require_string(self, document: Any, path: tuple[str, ...], *, actual: bool = False):
        value = get_path(document, path)
        dotted = ".".join(path)
        if value is MISSING:
            self.add(dotted, "is required")
        elif not isinstance(value, str):
            self.add(dotted, "must be a string")
        elif actual and is_fake(value):
            self.add(dotted, "must contain a non-placeholder deployment value")
        return value


def get_path(document: Any, path: Iterable[str]):
    current = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return MISSING
        current = current[key]
    return current


def has_path(document: Any, path: Iterable[str]) -> bool:
    return get_path(document, path) is not MISSING


def is_fake(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    placeholders = ("required", "changeme", "replace_me", "replace-me", "todo", "tbd")
    return (
        any(token in normalized for token in placeholders)
        or ".invalid" in normalized
        or ".example" in normalized
        or normalized in {"example", "placeholder"}
    )


def load_yaml(path: Path, errors: ValidationErrors, label: str | None = None):
    display = label or str(path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.add(display, "file not found")
        return None
    except UnicodeError:
        errors.add(display, "could not be decoded as UTF-8")
        return None
    except OSError:
        errors.add(display, "could not be read")
        return None
    try:
        document = yaml.load(content, Loader=UniqueKeyLoader)
    except DuplicateKeyError as error:
        errors.add(display, str(error))
        return None
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        errors.add(display, f"invalid YAML{location}")
        return None
    if not isinstance(document, dict):
        errors.add(display, "root must be a mapping")
        return None
    return document


def merge_values(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Apply recursive map coalescing; null removes an inherited key."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_values(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_values_stack(repo_root: Path, supplied: list[Path], errors: ValidationErrors):
    merged: dict[str, Any] = {}
    documents: list[tuple[Path, dict[str, Any]]] = []
    for service in SERVICES:
        path = repo_root / "src" / service / "chart" / "values.yaml"
        document = load_yaml(path, errors)
        if document is not None:
            merged = merge_values(merged, {service: document})
    umbrella = load_yaml(repo_root / "src" / "app" / "chart" / "values.yaml", errors)
    if umbrella is not None:
        merged = merge_values(merged, umbrella)
    for path in supplied:
        document = load_yaml(path, errors)
        if document is not None:
            documents.append((path, document))
            merged = merge_values(merged, document)
    return merged, documents


def _walk(document: Any, path: tuple[str, ...] = ()):
    if isinstance(document, dict):
        for key, value in document.items():
            child_path = path + (str(key),)
            yield child_path, value
            yield from _walk(value, child_path)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from _walk(value, path + (str(index),))


def validate_string_map(value: Any, path: tuple[str, ...], errors: ValidationErrors):
    dotted = ".".join(path)
    if not isinstance(value, dict):
        errors.add(dotted, "must be a mapping of string keys to string values")
        return
    for key, item in value.items():
        item_path = f"{dotted}.{key}"
        if not isinstance(key, str):
            errors.add(dotted, "must contain only string keys")
        if not isinstance(item, str):
            errors.add(item_path, "must be a string")


def validate_structure(values, environment, supplied_documents, errors):
    for path, value in _walk(values):
        key = path[-1]
        if key in {"podAnnotations", "annotations"}:
            validate_string_map(value, path, errors)
        elif path in BOOLEAN_PATHS and not isinstance(value, bool):
            errors.add(".".join(path), "must be a boolean")
        elif key in {"provider", "endpoint", "tableName", "secretName"} and not isinstance(value, str):
            errors.add(".".join(path), "must be a string")
        elif key == "name" and len(path) >= 2 and path[-2] == "secret" and not isinstance(value, str):
            errors.add(".".join(path), "must be a string")
        elif key == "endpoints":
            validate_string_map(value, path, errors)
        elif key == "image":
            if not isinstance(value, dict):
                errors.add(".".join(path), "must be a mapping")
            else:
                repository = value.get("repository", MISSING)
                tag = value.get("tag", MISSING)
                if repository is not MISSING and not isinstance(repository, str):
                    errors.add(".".join(path + ("repository",)), "must be a string")
                if tag is not MISSING and tag is not None and not isinstance(tag, str):
                    errors.add(".".join(path + ("tag",)), "must be a string or null")

    for path, supported in PROVIDERS.items():
        value = get_path(values, path)
        if isinstance(value, str) and value and value not in supported:
            errors.add(".".join(path), "is not supported by the current chart")

    registry = REGISTRIES[environment]
    for service in SERVICES:
        repository_path = (service, "image", "repository")
        repository = get_path(values, repository_path)
        expected = f"{registry}/retail-{service}"
        if repository is MISSING:
            errors.add(".".join(repository_path), "is required")
        elif isinstance(repository, str) and repository != expected:
            errors.add(".".join(repository_path), f"must use the {environment.upper()} retail registry")

    for path, document in supplied_documents:
        if path.name.lower() != "versions.yaml":
            continue
        for service in SERVICES:
            tag_path = (service, "image", "tag")
            tag = get_path(document, tag_path)
            if not isinstance(tag, str) or not IMMUTABLE_TAG.fullmatch(tag):
                errors.add(".".join(tag_path), "must be an immutable vMAJOR.MINOR.PATCH tag")

    if environment == "gcp":
        validate_no_aws_settings(values, errors)


def validate_no_aws_settings(values: dict[str, Any], errors: ValidationErrors):
    for path, value in _walk(values):
        dotted = ".".join(path)
        key = path[-1].lower()
        if "eks.amazonaws.com/" in key or "alb.ingress.kubernetes.io/" in key:
            errors.add(dotted, "AWS-specific settings are not allowed in GCP values")
        if key == "classname" and isinstance(value, str) and value.strip().lower() == "alb":
            errors.add(dotted, "AWS ALB ingress settings are not allowed in GCP values")
        path_keys = {part.lower() for part in path}
        if path_keys.intersection({"endpoint", "endpoints"}) and isinstance(value, str):
            normalized = value.lower()
            if ".amazonaws.com" in normalized or normalized.startswith("arn:aws:"):
                errors.add(dotted, "AWS endpoints are not allowed in GCP values")


def validate_host_port(value: Any, path: tuple[str, ...], errors: ValidationErrors):
    dotted = ".".join(path)
    if not isinstance(value, str) or is_fake(value):
        errors.add(dotted, "must contain a non-placeholder host:port deployment value")
    elif not HOST_PORT.fullmatch(value):
        errors.add(dotted, "must use host:port without a URL scheme")
    elif int(value.rsplit(":", 1)[1]) > 65535:
        errors.add(dotted, "must use a valid host:port")


def is_forbidden_deployment_path(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    return path.name.lower().endswith(".example.yaml") or any(
        parts[index:index + 2] == ["ci", "fixtures"]
        for index in range(len(parts) - 1)
    )


def validate_deployment_values(values, environment, supplied_documents, supplied_paths, errors):
    for path in supplied_paths:
        if is_forbidden_deployment_path(path):
            errors.add(str(path), "is not an allowed deployment input path")

    for service in SERVICES:
        tag_path = (service, "image", "tag")
        tag = get_path(values, tag_path)
        if not isinstance(tag, str) or not IMMUTABLE_TAG.fullmatch(tag):
            errors.add(".".join(tag_path), "must be explicitly set to immutable vMAJOR.MINOR.PATCH")

    for path, supported in PROVIDERS.items():
        value = get_path(values, path)
        if path == ("catalog", "app", "search", "provider") and get_path(
            values, ("catalog", "app", "search", "enabled")
        ) is not True:
            continue
        if path == ("ui", "app", "chat", "provider") and get_path(
            values, ("ui", "app", "chat", "enabled")
        ) is not True:
            continue
        if value is MISSING or not isinstance(value, str) or is_fake(value):
            errors.add(".".join(path), "must select a supported provider explicitly")
        elif value not in supported:
            errors.add(".".join(path), "is not supported by the current chart")

    checkout_provider = get_path(values, ("checkout", "app", "persistence", "provider"))
    if checkout_provider == "redis":
        if get_path(values, ("checkout", "redis", "create")) is not False:
            errors.add("checkout.redis.create", "must be false for the external deployment Redis")
        validate_host_port(get_path(values, ("checkout", "app", "persistence", "redis", "endpoint")), ("checkout", "app", "persistence", "redis", "endpoint"), errors)
        tls = get_path(values, ("checkout", "app", "persistence", "redis", "tls"))
        if not isinstance(tls, bool):
            errors.add("checkout.app.persistence.redis.tls", "must be a boolean")
    else:
        errors.add("checkout.app.persistence.provider", "must select external redis for deployment")

    validate_host_port(get_path(values, ("ui", "app", "session", "redis", "endpoint")), ("ui", "app", "session", "redis", "endpoint"), errors)
    ui_tls = get_path(values, ("ui", "app", "session", "redis", "tls"))
    if ui_tls is True:
        errors.add("ui.app.session.redis.tls", "true is unsupported because the current UI chart always emits redis://")
    elif ui_tls is not MISSING and not isinstance(ui_tls, bool):
        errors.add("ui.app.session.redis.tls", "must be a boolean")

    if get_path(values, ("orders", "app", "persistence", "provider")) == "postgres":
        if get_path(values, ("orders", "postgresql", "create")) is not False:
            errors.add("orders.postgresql.create", "must be false for external Postgres")
        validate_host_port(get_path(values, ("orders", "app", "persistence", "endpoint")), ("orders", "app", "persistence", "endpoint"), errors)
        errors.require_string(values, ("orders", "app", "persistence", "database"), actual=True)
        if get_path(values, ("orders", "app", "persistence", "secret", "create")) is not False:
            errors.add("orders.app.persistence.secret.create", "must be false so an existing Secret is referenced")
        errors.require_string(values, ("orders", "app", "persistence", "secret", "name"), actual=True)

    cart_provider = get_path(values, ("cart", "app", "persistence", "provider"))
    if environment == "gcp" and not any(has_path(document, ("cart", "app", "persistence", "provider")) for _, document in supplied_documents):
        errors.add("cart.app.persistence.provider", "must be selected explicitly for GCP; no storage design is implied")
    if environment == "aws" and cart_provider == "dynamodb":
        if get_path(values, ("cart", "dynamodb", "create")) is not False:
            errors.add(
                "cart.dynamodb.create",
                "must be false because deployment uses the externally managed table",
            )
        if get_path(
            values, ("cart", "app", "persistence", "dynamodb", "createTable")
        ) is not False:
            errors.add(
                "cart.app.persistence.dynamodb.createTable",
                "must be false because table lifecycle is externally managed",
            )
        if get_path(values, ("cart", "serviceAccount", "create")) is not True:
            errors.add(
                "cart.serviceAccount.create",
                "must be true so the current chart emits the IRSA-annotated ServiceAccount",
            )
        errors.require_string(values, ("cart", "app", "persistence", "dynamodb", "tableName"), actual=True)
        role_path = ("cart", "serviceAccount", "annotations", "eks.amazonaws.com/role-arn")
        role = get_path(values, role_path)
        if not isinstance(role, str) or is_fake(role):
            errors.add(".".join(role_path), "must contain a non-placeholder IRSA role ARN")
        else:
            match = IRSA_ROLE.fullmatch(role)
            if match is None or match.group(1) == "000000000000":
                errors.add(".".join(role_path), "must be a valid non-placeholder IRSA role ARN")
    if environment == "gcp" and cart_provider != "in-memory":
        errors.add(
            "cart.app.persistence.provider",
            "GCP Cart persistence requires a separately reviewed data/identity design",
        )

    for service in ("catalog", "checkout"):
        if get_path(values, (service, "whatap", "enabled")) is True:
            errors.require_string(values, (service, "whatap", "secretName"), actual=True)
            errors.require_string(values, (service, "whatap", "serverHost"), actual=True)


def validate_application(application, environment, repo_root, supplied_paths, errors):
    if "operation" in application:
        errors.add(
            "application.operation",
            "must be absent because it can request sync outside the manual-sync policy",
        )

    exact_values = {
        ("apiVersion",): "argoproj.io/v1alpha1",
        ("kind",): "Application",
        ("spec", "source", "repoURL"): REPOSITORY,
        ("spec", "source", "path"): "src/app/chart",
    }
    for path, expected in exact_values.items():
        if get_path(application, path) != expected:
            errors.add("application." + ".".join(path), "has an unexpected value")

    for path in (("metadata", "name"), ("metadata", "namespace"), ("spec", "project"), ("spec", "source", "targetRevision"), ("spec", "source", "helm", "releaseName"), ("spec", "destination", "namespace")):
        value = get_path(application, path)
        dotted = "application." + ".".join(path)
        if value is MISSING or not isinstance(value, str):
            errors.add(dotted, "must be a string")
        elif is_fake(value):
            errors.add(dotted, "must contain a non-placeholder value")

    expected_value_files = [f"../../../environments/{environment}/values.yaml", "versions.yaml"]
    if get_path(application, ("spec", "source", "helm", "valueFiles")) != expected_value_files:
        errors.add("application.spec.source.helm.valueFiles", "must list the environment values and versions.yaml in the required order")

    expected_inputs = [(repo_root / "environments" / environment / "values.yaml").resolve(), (repo_root / "src" / "app" / "chart" / "versions.yaml").resolve()]
    if [path.resolve() for path in supplied_paths] != expected_inputs:
        errors.add("-f input stack", "must resolve to the same two files used by the Application")

    destination = get_path(application, ("spec", "destination"))
    if isinstance(destination, dict):
        selectors = []
        for key in ("name", "server"):
            value = destination.get(key, MISSING)
            if isinstance(value, str) and not is_fake(value):
                selectors.append(key)
            elif value is not MISSING:
                errors.add(f"application.spec.destination.{key}", "must be a non-placeholder string")
        if len(selectors) != 1:
            errors.add("application.spec.destination", "must set exactly one of name or server")
    else:
        errors.add("application.spec.destination", "must be a mapping")

    spec = get_path(application, ("spec",))
    if isinstance(spec, dict) and "sources" in spec:
        errors.add("application.spec.sources", "multi-source Applications are not allowed")

    helm = get_path(application, ("spec", "source", "helm"))
    if isinstance(helm, dict):
        for key in ("values", "valuesObject", "parameters", "fileParameters"):
            if key in helm:
                errors.add(f"application.spec.source.helm.{key}", "inline or parameter values are not allowed")
        for key in ("ignoreMissingValueFiles", "skipSchemaValidation"):
            if helm.get(key) is True:
                errors.add(f"application.spec.source.helm.{key}", "must not be true")
    else:
        errors.add("application.spec.source.helm", "must be a mapping")

    finalizers = get_path(application, ("metadata", "finalizers"))
    if isinstance(finalizers, list) and any(isinstance(item, str) and item.startswith("resources-finalizer.argocd.argoproj.io") for item in finalizers):
        errors.add("application.metadata.finalizers", "resources finalizers are not allowed in the draft")

    sync_policy = get_path(application, ("spec", "syncPolicy"))
    if sync_policy is not MISSING:
        if not isinstance(sync_policy, dict):
            errors.add("application.spec.syncPolicy", "must be a mapping")
        else:
            if "automated" in sync_policy:
                automated = sync_policy["automated"]
                if not isinstance(automated, dict) or automated.get("enabled") is not False:
                    errors.add("application.spec.syncPolicy.automated", "must be absent or explicitly disabled with enabled: false")
            sync_options = sync_policy.get("syncOptions", [])
            if not isinstance(sync_options, list):
                errors.add("application.spec.syncPolicy.syncOptions", "must be a list")
            elif any(isinstance(option, str) and option.replace(" ", "").lower() in {"force=true", "replace=true"} for option in sync_options):
                errors.add("application.spec.syncPolicy.syncOptions", "Force=true and Replace=true are not allowed in the draft")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("aws", "gcp"), required=True)
    parser.add_argument("--mode", choices=("structure", "deployment"), required=True)
    parser.add_argument("-f", "--file", action="append", required=True, dest="files")
    parser.add_argument("--application")
    return parser


def main(argv=None, *, repo_root=None):
    args = build_parser().parse_args(argv)
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    supplied_paths = [Path(item).resolve() for item in args.files]
    errors = ValidationErrors()
    values, supplied_documents = load_values_stack(root, supplied_paths, errors)
    validate_structure(values, args.environment, supplied_documents, errors)
    if args.mode == "deployment":
        validate_deployment_values(values, args.environment, supplied_documents, supplied_paths, errors)
        if args.application is None:
            errors.add("--application", "is required in deployment mode")
        else:
            application_path = Path(args.application).resolve()
            expected_application = (
                root / "applications" / f"{args.environment}.yaml"
            ).resolve()
            if application_path != expected_application:
                errors.add(
                    "--application",
                    f"must resolve to applications/{args.environment}.yaml",
                )
            if is_forbidden_deployment_path(application_path):
                errors.add("application", "is not an allowed deployment input path")
            application = load_yaml(application_path, errors, f"application ({application_path})")
            if application is not None:
                validate_application(application, args.environment, root, supplied_paths, errors)
    if errors.items:
        print("ERROR: validation failed:", file=sys.stderr)
        for error in errors.items:
            print(f"- {error}", file=sys.stderr)
        return 1
    message = "structural validation succeeded" if args.mode == "structure" else "deployment input validation succeeded"
    print(f"offline {message}; runtime unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
