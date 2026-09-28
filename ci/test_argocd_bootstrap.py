"""Offline safety checks for the AWS Argo CD bootstrap."""

import json
import shlex
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TRUST_POLICY = ROOT / "bootstrap/argocd/github-oidc-trust-policy.json"
VALUES = ROOT / "bootstrap/argocd/values-aws.yaml"
SSM_DOCUMENT = ROOT / "bootstrap/argocd/ssm-install-aws.yaml"


class ArgoBootstrapTests(unittest.TestCase):
    def test_values_select_only_mgmt_and_tolerate_its_taint(self):
        values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "nodeSelector": {"kubernetes.io/os": "linux", "workload": "mgmt"},
                "tolerations": [
                    {
                        "key": "dedicated",
                        "operator": "Equal",
                        "value": "mgmt",
                        "effect": "NoSchedule",
                    }
                ],
            },
            values.get("global"),
        )

    def test_values_preserve_private_access_and_non_ha_redis(self):
        values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        values.pop("global", None)
        self.assertEqual(
            {
                "server": {"service": {"type": "ClusterIP"}, "ingress": {"enabled": False}},
                "redis-ha": {"enabled": False},
            },
            values,
        )

    def test_ssm_fixed_install_snapshot_matches_approved_values(self):
        document = yaml.safe_load(SSM_DOCUMENT.read_text(encoding="utf-8"))
        script = document["mainSteps"][0]["inputs"]["runCommand"][0]
        install = script.split('"$ArgoHelm" install argocd "$ArgoWork/argo-cd.tgz"', 1)[1]
        arguments = shlex.split(install.split("\n\n", 1)[0].replace("\\\n", ""))
        overrides = [
            (argument, arguments[index + 1])
            for index, argument in enumerate(arguments)
            if argument in ("--set", "--set-string")
        ]
        self.assertEqual(
            [
                ("--set", "server.service.type=ClusterIP"),
                ("--set", "server.ingress.enabled=false"),
                ("--set", "redis-ha.enabled=false"),
                ("--set-string", r"global.nodeSelector.kubernetes\.io/os=linux"),
                ("--set-string", "global.nodeSelector.workload=mgmt"),
                ("--set-string", "global.tolerations[0].key=dedicated"),
                ("--set-string", "global.tolerations[0].operator=Equal"),
                ("--set-string", "global.tolerations[0].value=mgmt"),
                ("--set-string", "global.tolerations[0].effect=NoSchedule"),
            ],
            overrides,
        )

    def test_oidc_trust_allows_only_gitops_main(self):
        self.assertTrue(
            TRUST_POLICY.is_file(),
            "Trust policy file is missing",
        )

        expected = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Federated": (
                            "arn:aws:iam::350606136784:"
                            "oidc-provider/token.actions.githubusercontent.com"
                        )
                    },
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": (
                                "sts.amazonaws.com"
                            ),
                            "token.actions.githubusercontent.com:sub": (
                                "repo:AWSome-BIT-Bespin@323029296/"
                                "retail-store-gitops@1352059385:"
                                "ref:refs/heads/main"
                            ),
                        }
                    },
                }
            ],
        }

        actual = json.loads(TRUST_POLICY.read_text(encoding="utf-8"))
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
