"""Offline safety checks for the AWS Argo CD bootstrap."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRUST_POLICY = ROOT / "bootstrap/argocd/github-oidc-trust-policy.json"


class ArgoBootstrapTests(unittest.TestCase):
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
