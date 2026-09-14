# retail-store-gitops

Retail Store의 공통 Helm 차트와 AWS/GCP 배포 설정을 관리한다. 이미지 빌드·ECR/GAR 게시는 `retail-store-app`의 GitHub Actions가 담당한다.

현재 추가된 환경 파일과 Application은 **입력 예제**다. 실제 클러스터에 등록하거나 배포한 결과가 아니다. 구조 CI가 통과해도 배포 준비가 끝났다는 뜻은 아니다.

| 경로 | 역할 |
|---|---|
| `src/app/chart/values.yaml` | 공통 서비스 이름과 내부 연결 |
| `src/app/chart/versions.yaml` | 검증한 이미지 버전 기록. 환경 values 뒤에 적용 |
| `environments/aws/values.example.yaml` | AWS 실제값을 채울 입력 양식 |
| `environments/gcp/values.example.yaml` | GCP 실제값과 미결정 데이터 구성을 채울 입력 양식 |
| `applications/*.example.yaml` | Argo CD Application 초안. 대상 정보는 미입력 |
| `ci/fixtures/` | 렌더링 전용 가상값. 배포에 사용 금지 |
| `bootstrap/argocd/values-aws.yaml` | 기존 Argo CD 설치용 values. 설치 완료 증거는 아님 |

준비 순서는 [필수 입력과 담당 영역](runbooks/cd-inputs.md), [이미지 릴리스 인계](runbooks/release-handoff.md), [배포와 롤백 절차](runbooks/deploy-and-rollback.md)를 따른다. 담당 영역은 역할별 제안이며 개인에게 확정 배정한 목록은 아니다.

## 로컬 구조 검증

GitOps 저장소 루트, Python 3.12 이상과 Helm 4.2.3이 있는 PowerShell에서 실행한다.

```powershell
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed' }
.venv/Scripts/python.exe -m pip install -r ci/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Validation dependency installation failed' }
.venv/Scripts/python.exe -m unittest discover -s ci -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'CD regression tests failed' }
helm dependency build ./src/app/chart
if ($LASTEXITCODE -ne 0) { throw 'Helm dependency build failed' }
New-Item -ItemType Directory -Force .validation-output | Out-Null
foreach ($cloud in @('aws', 'gcp')) {
    $values = @('-f', "environments/$cloud/values.example.yaml", '-f', "ci/fixtures/$cloud.yaml", '-f', 'src/app/chart/versions.yaml')
    .venv/Scripts/python.exe ci/validate_cd.py --environment $cloud --mode structure @values
    if ($LASTEXITCODE -ne 0) { throw "$cloud structure validation failed" }
    helm lint ./src/app/chart --with-subcharts @values
    if ($LASTEXITCODE -ne 0) { throw "$cloud Helm lint failed" }
    helm template cd-structure ./src/app/chart --namespace cd-structure @values > ".validation-output/$cloud-structure.yaml"
    if ($LASTEXITCODE -ne 0) { throw "$cloud Helm render failed" }
    .venv/Scripts/python.exe ci/check_rendered.py ".validation-output/$cloud-structure.yaml" --environment $cloud
    if ($LASTEXITCODE -ne 0) { throw "$cloud rendered Kubernetes type check failed" }
}
```

주소의 `.example.invalid`와 시험용 IAM ARN은 실제 리소스가 아니다. GCP 시험의 Cart 인메모리는 차트 검사만을 위한 선택이며 DR 데이터 설계로 확정하지 않았다.

## CI 결과 읽기

PR, main push, 수동 실행에서 타입 검사·회귀 테스트·AWS/GCP 구조 lint/render와 기존 Argo CD bootstrap 검사를 수행한다. 최종 렌더링에서는 annotation과 ConfigMap 데이터가 Kubernetes 문자열 타입에 맞는지도 확인하고, GCP에 AWS 보안그룹 같은 전용 리소스·IRSA·ALB·private ECR 설정이 섞이면 거부한다. 실제 `environments/<cloud>/values.yaml` 또는 `applications/<cloud>.yaml` 중 하나라도 생기면 두 파일 모두를 요구하고 배포 입력 검사와 렌더링을 수행한다. 둘 다 없으면 실행 요약에 `NOT CONFIGURED`를 표시한다.

수동 실행의 `deployment_environment`를 `aws` 또는 `gcp`로 지정하면 실제 파일이 없어도 해당 입력 검사를 요구하므로, 현재 미구성 상태에서는 실패하는 것이 맞다. `structure`는 시험값으로 검증하는 기본 선택이다. 어느 모드도 이미지 게시·Argo 등록·클러스터 적용을 실행하지 않는다.

기존 `src/app/chart/values-dev-rds.yaml`은 미완성인 이전 환경 경로다. 배열 타입 오류 두 곳은 빈 문자열로 바꾸었지만 endpoint와 IAM 값은 채우지 않았다. 새 Application은 이 파일을 참조하지 않는다. 실제 배포가 기존 경로를 사용 중이라면 새 경로로 바꾸기 전에 담당자와 현재 값을 대조해야 한다.
