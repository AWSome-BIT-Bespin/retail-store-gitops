"""Regressions for Kubernetes value types after Helm removes override tombstones."""
import unittest

from check_rendered import check_documents


class RenderedTypesTest(unittest.TestCase):
    def test_accepts_annotation_removal_and_string_config_values(self):
        documents = [
            {'kind': 'ServiceAccount', 'metadata': {'name': 'carts', 'annotations': {}}},
            {'kind': 'ConfigMap', 'metadata': {'name': 'orders'}, 'data': {'PORT': '5432'}},
        ]
        self.assertEqual([], check_documents(documents))

    def test_rejects_annotation_tombstone_left_in_output(self):
        document = {'kind': 'ServiceAccount', 'metadata': {'annotations': {'role': None}}}
        self.assertTrue(any('annotations.role' in error for error in check_documents([document])))

    def test_rejects_numeric_pod_annotation(self):
        document = {'kind': 'Deployment', 'spec': {'template': {'metadata': {'annotations': {'port': 8080}}}}}
        self.assertTrue(any('annotations.port' in error for error in check_documents([document])))

    def test_rejects_unquoted_empty_or_boolean_config_values(self):
        for value in (None, False, [], {}):
            with self.subTest(value=value):
                document = {'kind': 'ConfigMap', 'data': {'ENDPOINT': value}}
                self.assertTrue(any('data.ENDPOINT' in error for error in check_documents([document])))

    def test_ignores_secret_values(self):
        document = {'kind': 'Secret', 'data': {'private': 'do-not-print-this'}}
        self.assertEqual([], check_documents([document]))

    def test_rejects_empty_output(self):
        self.assertTrue(check_documents([None]))


if __name__ == '__main__':
    unittest.main()
