"""Check selected Kubernetes types after Helm's value merging and rendering."""
import argparse
import sys
from pathlib import Path

import yaml


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
    print('Rendered annotation and ConfigMap types passed; server validation and runtime remain unverified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
