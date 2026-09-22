"""Exercise Actions wiring offline; Helm/validator internals have separate tests.

Run the actual Bash step bodies with recording command boundaries. This tests
selection, argument forwarding and failure propagation without deploying,
downloading charts or requiring an Argo CD Application for the real GCP target.
"""

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/helm-ci.yml"
GCP_VALUES = "environments/gcp/values.yaml"
VERSIONS = "src/app/chart/versions.yaml"
GCP_STEP = "Validate, lint and render GCP DR values"
DEPLOYMENT_STEP = "Validate configured or explicitly requested deployment inputs"


def find_bash():
    # Windows' system bash.exe may be a WSL launcher, not a usable Bash shell.
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin/bash.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


class HelmWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader preserves the GitHub Actions key "on" (YAML 1.1 calls it True).
        cls.workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        cls.steps = cls.workflow["jobs"]["helm-validate"]["steps"]
        cls.bash = find_bash()

    def step(self, name):
        matches = [step for step in self.steps if step.get("name") == name]
        self.assertEqual(1, len(matches), f"Expected one workflow step: {name}")
        return matches[0]

    def run_step(self, step, *, files=(), requested="structure", fail_match=""):
        self.assertIsNotNone(self.bash, "Workflow wiring tests require Bash (Git Bash on Windows).")
        with tempfile.TemporaryDirectory(prefix="helm-workflow-") as temporary:
            root = Path(temporary)
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            # Only external command boundaries are replaced. The workflow's own
            # shell conditions, variables, arrays, ordering and summaries execute.
            boundary = textwrap.dedent(r"""
                record_command() {
                  printf '%s' "$1" >> commands.log
                  shift
                  printf '\t%s' "$@" >> commands.log
                  printf '\n' >> commands.log
                  if [[ -n "$TEST_FAIL_MATCH" && "$*" == *"$TEST_FAIL_MATCH"* ]]; then
                    return 23
                  fi
                }
                python() {
                  if [[ "$1" == "-" ]]; then
                    printf '%s\t%s\n' "$CLOUD" "${VALUES_FILE-}" >> inline-env.log
                  fi
                  record_command python "$@"
                }
                helm() { record_command helm "$@"; }
            """)
            env = dict(os.environ, REQUESTED_ENVIRONMENT=requested,
                       RUNNER_TEMP=".", GITHUB_STEP_SUMMARY="summary.txt",
                       TEST_FAIL_MATCH=fail_match, BASH_ENV="")
            result = subprocess.run(
                [self.bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", boundary + step["run"]],
                cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15,
            )

            def read(name):
                path = root / name
                return path.read_text(encoding="utf-8") if path.exists() else ""

            calls = [line.split("\t") for line in read("commands.log").splitlines()]
            return result, calls, read("summary.txt"), read("inline-env.log")

    @staticmethod
    def values_arguments(command):
        return [command[index + 1] for index, arg in enumerate(command) if arg == "-f"]

    @staticmethod
    def validation_commands(calls):
        return [call for call in calls if call[:2] == ["python", "ci/validate_cd.py"]]

    def test_gcp_values_run_unconditionally_after_dependency_build(self):
        step = self.step(GCP_STEP)
        self.assertNotIn("if", step)
        self.assertEqual("bash", step["shell"])
        self.assertLess(self.steps.index(self.step("Build Helm dependencies")), self.steps.index(step))
        self.assertLess(self.steps.index(step), self.steps.index(self.step(DEPLOYMENT_STEP)))

    def test_gcp_values_validate_lint_render_and_check_in_order(self):
        result, calls, summary, _ = self.run_step(self.step(GCP_STEP))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(4, len(calls), calls)
        self.assertEqual(["python", "ci/validate_cd.py", "--environment", "gcp", "--mode", "values"], calls[0][:6])
        self.assertEqual(["helm", "lint", "./src/app/chart", "--with-subcharts"], calls[1][:4])
        self.assertEqual(["helm", "template", "cd-gcp-values", "./src/app/chart", "--namespace", "cd-gcp-values"], calls[2][:6])
        for command in calls[:3]:
            self.assertEqual([GCP_VALUES, VERSIONS], self.values_arguments(command))
            self.assertNotIn("--application", command)
        self.assertEqual(["python", "ci/check_rendered.py", "./retail-gcp-values.yaml", "--environment", "gcp"], calls[3])
        self.assertIn("gcp: offline values and rendering passed", summary)
        self.assertIn("unverified", summary)

    def test_gcp_values_failure_stops_without_success_summary(self):
        for failing in ("--mode values", "lint ./src/app/chart", "template cd-gcp-values", "ci/check_rendered.py"):
            with self.subTest(failing=failing):
                result, _, summary, _ = self.run_step(self.step(GCP_STEP), fail_match=failing)
                self.assertEqual(23, result.returncode, result.stderr)
                self.assertNotIn("passed", summary)

    def test_normal_run_with_gcp_values_does_not_require_gcp_application(self):
        result, calls, summary, _ = self.run_step(
            self.step(DEPLOYMENT_STEP),
            files=(GCP_VALUES, "environments/aws/values.yaml", "applications/aws.yaml"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        commands = self.validation_commands(calls)
        self.assertEqual(1, len(commands), commands)
        self.assertIn("aws", commands[0])
        self.assertIn("gcp: Application NOT CONFIGURED", summary)
        self.assertNotIn("only structure", summary)
        self.assertNotIn("environments/gcp/values-dr.yaml", summary)
        self.assertIn("unverified", summary)

    def test_explicit_gcp_request_still_requires_full_inputs_and_propagates_failure(self):
        result, calls, summary, _ = self.run_step(
            self.step(DEPLOYMENT_STEP), files=(GCP_VALUES,), requested="gcp",
            fail_match="--environment gcp --mode deployment",
        )
        self.assertEqual(23, result.returncode, result.stderr)
        commands = self.validation_commands(calls)
        self.assertEqual(1, len(commands), commands)
        self.assertEqual([GCP_VALUES, VERSIONS], self.values_arguments(commands[0]))
        self.assertEqual(["--application", "applications/gcp.yaml"], commands[0][-2:])
        self.assertNotIn("gcp: offline deployment inputs and rendering passed", summary)

    def test_configured_gcp_application_uses_current_values_for_both_validators(self):
        result, calls, summary, inline_env = self.run_step(
            self.step(DEPLOYMENT_STEP), files=(GCP_VALUES, "applications/gcp.yaml"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        commands = self.validation_commands(calls)
        self.assertEqual(1, len(commands), commands)
        self.assertEqual([GCP_VALUES, VERSIONS], self.values_arguments(commands[0]))
        self.assertIn(f"gcp\t{GCP_VALUES}\n", inline_env)
        self.assertIn("gcp: offline deployment inputs and rendering passed", summary)
        self.assertIn("runtime behavior remain unverified", summary)

    def test_gcp_application_without_values_is_checked_not_skipped(self):
        result, calls, _, _ = self.run_step(
            self.step(DEPLOYMENT_STEP), files=("applications/gcp.yaml",),
            fail_match="--environment gcp --mode deployment",
        )
        self.assertEqual(23, result.returncode, result.stderr)
        self.assertEqual(1, len(self.validation_commands(calls)))

    def test_aws_selection_conditions_are_preserved(self):
        for files, requested in ((["environments/aws/values.yaml"], "structure"),
                                 (["applications/aws.yaml"], "structure"), ([], "aws")):
            with self.subTest(files=files, requested=requested):
                result, calls, _, _ = self.run_step(
                    self.step(DEPLOYMENT_STEP), files=files, requested=requested,
                    fail_match="--environment aws --mode deployment",
                )
                self.assertEqual(23, result.returncode, result.stderr)
                commands = self.validation_commands(calls)
                self.assertEqual(1, len(commands), commands)
                self.assertEqual(["environments/aws/values.yaml", VERSIONS], self.values_arguments(commands[0]))

    def test_inline_deployment_render_uses_forwarded_values_and_application_context(self):
        script = self.step(DEPLOYMENT_STEP)["run"].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        for cloud, values_file in (("aws", "environments/aws/values.yaml"), ("gcp", GCP_VALUES)):
            with self.subTest(cloud=cloud), tempfile.TemporaryDirectory(prefix="helm-inline-") as temporary:
                root = Path(temporary)
                (root / "applications").mkdir()
                spec = {"source": {"helm": {"releaseName": f"test-{cloud}"}},
                        "destination": {"namespace": f"test-{cloud}-namespace"}}
                (root / f"applications/{cloud}.yaml").write_text(yaml.safe_dump({"spec": spec}), encoding="utf-8")
                env = {"CLOUD": cloud, "VALUES_FILE": values_file, "RUNNER_TEMP": str(root)}
                # Execute real embedded Python; replace only its subprocess boundary.
                with chdir(root), patch.dict(os.environ, env), patch("subprocess.run") as run:
                    exec(compile(script, "helm-ci.yml:deployment", "exec"), {})
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(3, len(commands), commands)
                self.assertEqual(["helm", "lint", "./src/app/chart", "--with-subcharts"], commands[0][:4])
                self.assertEqual(["helm", "template", f"test-{cloud}", "./src/app/chart", "--namespace", f"test-{cloud}-namespace"], commands[1][:6])
                for command in commands[:2]:
                    self.assertEqual([values_file, VERSIONS], self.values_arguments(command))
                output = str(root / f"retail-{cloud}-deployment-inputs.yaml")
                self.assertEqual(["python", "ci/check_rendered.py", output, "--environment", cloud], commands[2])
                self.assertTrue(all(call.kwargs.get("check") is True for call in run.call_args_list))

    def test_trigger_permissions_and_manual_options_are_preserved(self):
        triggers = self.workflow["on"]
        self.assertEqual({"pull_request", "push", "workflow_dispatch"}, set(triggers))
        self.assertEqual(["main"], triggers["push"]["branches"])
        manual = triggers["workflow_dispatch"]["inputs"]["deployment_environment"]
        self.assertEqual("structure", manual["default"])
        self.assertEqual(["structure", "aws", "gcp"], manual["options"])
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        for job in self.workflow["jobs"].values():
            self.assertNotIn("permissions", job)

    def test_structure_fixtures_tests_and_bootstrap_remain_wired(self):
        fixtures = self.step("Validate, lint and render AWS and GCP structure fixtures")["run"]
        self.assertIn("for cloud in aws gcp", fixtures)
        self.assertIn("environments/$cloud/values.example.yaml", fixtures)
        self.assertIn("ci/fixtures/$cloud.yaml", fixtures)
        self.assertIn("--mode structure", fixtures)
        self.assertIn("ci/check_rendered.py", fixtures)
        self.assertIn("unittest discover -s ci -p 'test_*.py'", self.step("Test CD input validation")["run"])
        bootstrap = self.workflow["jobs"]["argocd-bootstrap-validate"]
        steps = {step["name"]: step for step in bootstrap["steps"]}
        self.assertIn("--version 10.9.2", steps["Pull pinned Argo CD chart"]["run"])
        self.assertIn("./bootstrap/argocd/values-aws.yaml", steps["Lint Argo CD bootstrap"]["run"])

    def test_workflow_does_not_add_deployment_commands_or_cloud_authentication(self):
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                with self.subTest(step=step["name"]):
                    self.assertNotRegex(step.get("run", ""), r"\b(?:kubectl|terraform|gcloud|aws|argocd)\s+(?:apply|sync|login|configure|update|install)\b|\bhelm\s+(?:install|upgrade|uninstall)\b")
                    self.assertNotRegex(step.get("uses", ""), r"configure-aws-credentials|google-github-actions/auth")


if __name__ == "__main__":
    unittest.main()
