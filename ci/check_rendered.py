"""Check selected Kubernetes types after Helm's value merging and rendering."""
import argparse
import sys
from pathlib import Path

import yaml


def _field(document, *keys):
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def _mappings(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _container_value(container, variable, namespace, objects):
    """Resolve a literal env value or referenced ConfigMap; never read Secrets."""
    explicit = [entry for entry in _mappings(container.get('env')) if entry.get('name') == variable]
    if explicit:
        if len(explicit) != 1 or 'valueFrom' in explicit[0]:
            return None
        return explicit[0].get('value')
    value = None
    for source in _mappings(container.get('envFrom')):
        prefix = source.get('prefix', '')
        if not isinstance(prefix, str):
            return None
        if not variable.startswith(prefix):
            continue
        if 'secretRef' in source:
            return None  # The effective value cannot be proved offline.
        name = _field(source, 'configMapRef', 'name')
        if not name:
            continue
        configs = [doc for doc in objects if doc.get('kind') == 'ConfigMap'
                   and _field(doc, 'metadata', 'name') == name
                   and (_field(doc, 'metadata', 'namespace') or '') == namespace]
        if len(configs) != 1:
            return None
        data = configs[0].get('data')
        key = variable[len(prefix):]
        if isinstance(data, dict) and key in data:
            value = data[key]
    return value


def check_gcp_internal_redis(objects):
    """Check shared Redis only when this render contains chart-managed Redis.

    External Redis renders have no such Service/Deployment and retain their
    existing checks. This is a wiring check, not proof that Redis is healthy.
    """
    objects = [doc for doc in objects if isinstance(doc, dict)]
    internal = [doc for doc in objects if doc.get('kind') in ('Service', 'Deployment')
                and _field(doc, 'metadata', 'labels', 'app.kubernetes.io/component') == 'redis'
                and _field(doc, 'metadata', 'labels', 'app.kubernetes.io/owner') == 'retail-store-sample']
    if not internal:
        return []
    services = [doc for doc in internal if doc['kind'] == 'Service']
    deployments = [doc for doc in internal if doc['kind'] == 'Deployment']
    if len(services) != 1 or len(deployments) != 1:
        return ['GCP internal Redis: must render exactly one Redis Service and Deployment']
    service, deployment = services[0], deployments[0]
    name = _field(service, 'metadata', 'name')
    namespace = _field(service, 'metadata', 'namespace') or ''
    ports = [item.get('port') for item in _mappings(_field(service, 'spec', 'ports'))
             if item.get('name') == 'redis']
    if not isinstance(name, str) or not name or len(ports) != 1 or type(ports[0]) is not int or not 1 <= ports[0] <= 65535:
        return ['GCP internal Redis: Service must have a name and one valid Redis port']
    labels = _field(deployment, 'spec', 'template', 'metadata', 'labels')
    selector = _field(service, 'spec', 'selector')
    if (not isinstance(selector, dict) or not selector or not isinstance(labels, dict)
            or not all(labels.get(key) == value for key, value in selector.items())
            or (_field(deployment, 'metadata', 'namespace') or '') != namespace):
        return ['GCP internal Redis: Service selector and namespace must target the Redis Deployment']

    errors = []
    expected_url = f'redis://{name}:{ports[0]}'
    for role, variable in (('checkout', 'RETAIL_CHECKOUT_PERSISTENCE_REDIS_URL'), ('ui', 'SPRING_DATA_REDIS_URL')):
        containers = [container for doc in objects if doc.get('kind') == 'Deployment'
                      and (_field(doc, 'metadata', 'namespace') or '') == namespace
                      for container in _mappings(_field(doc, 'spec', 'template', 'spec', 'containers'))
                      if container.get('name') == role]
        if len(containers) != 1:
            errors.append(f'GCP {role} Redis: must render exactly one application container in the Redis namespace')
            continue
        container = containers[0]
        if _container_value(container, variable, namespace, objects) != expected_url:
            errors.append(f'GCP {role} Redis: must reference the generated Redis Service and port without an unresolved Secret override')
        if role == 'ui' and _container_value(container, 'SPRING_SESSION_STORE_TYPE', namespace, objects) != 'redis':
            errors.append('GCP ui session: must use Redis when sharing the chart-managed Redis')
    return errors


def check_documents(documents, environment=None):
    errors = []
    objects = [document for document in documents if document is not None]
    if not objects:
        return ['rendered output: no Kubernetes objects']

    def string_map(value, path):
        if value is None:
            return
        if not isinstance(value, dict):
            errors.append(f'{path}: must be a mapping')
            return
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                errors.append(f'{path}.{key}: key and value must be strings')
            if environment == 'gcp' and isinstance(key, str) and key.startswith(('eks.amazonaws.com/', 'alb.ingress.kubernetes.io/')):
                errors.append(f'{path}.{key}: AWS annotations are not allowed for GCP')

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                child = f'{path}.{key}'
                if key == 'annotations':
                    string_map(item, child)
                elif environment == 'gcp' and key == 'image' and isinstance(item, str) and '.dkr.ecr.' in item:
                    errors.append(f'{child}: private ECR images are not allowed for GCP')
                else:
                    walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f'{path}[{index}]')

    for index, document in enumerate(objects):
        path = f'object[{index}]'
        if not isinstance(document, dict):
            errors.append(f'{path}: must be a mapping')
            continue
        api_version = document.get('apiVersion', '')
        if environment == 'gcp' and isinstance(api_version, str) and api_version.split('/')[0].endswith('.k8s.aws'):
            errors.append(f'{path}.apiVersion: AWS Kubernetes resources are not allowed for GCP')
        walk(document, path)
        if document.get('kind') == 'ConfigMap':
            string_map(document.get('data'), f'{path}.data')
    if environment == 'gcp':
        errors.extend(check_gcp_internal_redis(objects))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rendered_file', type=Path)
    parser.add_argument('--environment', choices=('aws', 'gcp'))
    args = parser.parse_args()
    try:
        documents = list(yaml.safe_load_all(args.rendered_file.read_text(encoding='utf-8')))
        errors = check_documents(documents, environment=args.environment)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        print('ERROR: cannot read valid rendered YAML; values are not printed', file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(f'ERROR: {error}' for error in errors), file=sys.stderr)
        return 1
    print('Rendered type and applicable Redis wiring checks passed; server validation and runtime remain unverified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
