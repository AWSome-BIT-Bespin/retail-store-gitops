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

    def test_rejects_aws_security_group_policy_for_gcp(self):
        document = {'apiVersion': 'vpcresources.k8s.aws/v1beta1', 'kind': 'SecurityGroupPolicy'}
        self.assertTrue(check_documents([document], environment='gcp'))
        self.assertEqual([], check_documents([document], environment='aws'))

    def test_rejects_rendered_aws_annotation_and_private_ecr_for_gcp(self):
        document = {
            'kind': 'Deployment',
            'spec': {'template': {
                'metadata': {'annotations': {'eks.amazonaws.com/role-arn': 'synthetic-role'}},
                'spec': {'containers': [{'image': '000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/retail-orders:v1.0.0'}]},
            }},
        }
        errors = check_documents([document], environment='gcp')
        self.assertTrue(any('annotations' in error for error in errors))
        self.assertTrue(any('image' in error for error in errors))

    def test_accepts_native_gcp_workload(self):
        document = {
            'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'spec': {'template': {'spec': {'containers': [
                {'image': 'asia-northeast3-docker.pkg.dev/kdt4-3/retail-store/retail-orders:v0.1.1'}
            ]}}},
        }
        self.assertEqual([], check_documents([document], environment='gcp'))


if __name__ == '__main__':
    unittest.main()
