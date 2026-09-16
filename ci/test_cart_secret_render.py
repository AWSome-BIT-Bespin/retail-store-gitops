"""Ensure GitOps preserves the existing external Cart table configuration."""
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('helm'), 'helm is required')
class CartSecretRenderTests(unittest.TestCase):
    def render(self, provider='dynamodb', name='runtime-test', key='table-name'):
        values = {'app': {'persistence': {
            'provider': provider,
            'dynamodb': {'tableNameSecret': {'name': name, 'key': key}},
        }}}
        result = subprocess.run(
            ['helm', 'template', 'retail-test', str(ROOT / 'src/cart/chart'),
             '--show-only', 'templates/deployment.yaml', '-f', '-'],
            input=yaml.safe_dump(values), capture_output=True, text=True, encoding='utf-8',
        )
        self.assertEqual(0, result.returncode, result.stderr)
        deployment = yaml.safe_load(result.stdout)
        return {env['name']: env for env in deployment['spec']['template']['spec']['containers'][0]['env']}

    def test_existing_table_secret_overrides_configmap_without_exposing_value(self):
        env = self.render()
        self.assertIn('JAVA_OPTS', env)
        self.assertEqual(
            {'name': 'RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME',
             'valueFrom': {'secretKeyRef': {'name': 'runtime-test', 'key': 'table-name'}}},
            env.get('RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME'),
        )

    def test_empty_reference_preserves_configmap_only_mode(self):
        self.assertNotIn('RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME', self.render(name=''))

    def test_in_memory_does_not_require_dynamodb_secret(self):
        self.assertNotIn('RETAIL_CART_PERSISTENCE_DYNAMODB_TABLE_NAME', self.render(provider='in-memory'))


if __name__ == '__main__':
    unittest.main()
