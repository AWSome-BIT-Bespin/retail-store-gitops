"""Check selected Kubernetes types after Helm's value merging and rendering."""
import argparse
import sys
from pathlib import Path

import yaml


def check_documents(documents):
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

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                child = f'{path}.{key}'
                if key == 'annotations':
                    string_map(item, child)
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
        walk(document, path)
        if document.get('kind') == 'ConfigMap':
            string_map(document.get('data'), f'{path}.data')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rendered_file', type=Path)
    args = parser.parse_args()
    try:
        documents = list(yaml.safe_load_all(args.rendered_file.read_text(encoding='utf-8')))
        errors = check_documents(documents)
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
