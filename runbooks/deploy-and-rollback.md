# 배포·검증·롤백 절차

이 절차는 준비된 파일을 실제 환경에 연결할 때 사용할 운영 문서다. 이번 CD 준비에서 아래 클러스터 명령을 실행한 것은 아니다. 실제값·접근 권한·이미지 게시·기동 조건을 먼저 확인한다. 저장소 구조 검사만 통과한 상태에서 Application을 적용하지 않는다.

## 1. 실제 입력 파일과 이미지 증거 준비

[필수 입력](cd-inputs.md)을 확인한 뒤 환경 예제를 `environments/aws/values.yaml`, Application 예제를 `applications/aws.yaml`로 완성한다. GCP는 경로의 `aws`를 `gcp`로 바꾼다. 비밀값은 파일에 넣지 않고 기존 Secret 이름으로 참조한다.

Application은 해당 repo의 `src/app/chart`를 읽고 다음 순서로 values를 적용한다. 마지막 파일의 값이 우선하므로 `versions.yaml` 뒤에 시험값이나 별도 image tag를 넣지 않는다. [Argo CD 공식 Helm 문서](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/).

```yaml
valueFiles:
  - ../../../environments/aws/values.yaml
  - versions.yaml
```

[릴리스 인계 기록](release-handoff.md)을 확인한다. 새 Catalog 이미지는 WhaTap 기동 문제가 해결·재검증되어야 한다. TLS가 필요한 Redis라면 UI 차트 지원을 먼저 해결한다. 실제 환경 파일과 Application은 GitOps PR로 검토하며 targetRevision이 그 변경을 포함해야 한다.

## 2. 오프라인 입력 검사와 렌더링

실행 위치는 GitOps 저장소 루트다. README의 가상환경 설치 후 PowerShell에서 실행한다. 아래 예는 AWS용이며 클라우드나 Kubernetes에 접속하지 않는다.

```powershell
$cloud = 'aws'
.venv/Scripts/python.exe ci/validate_cd.py --environment $cloud --mode deployment `
    -f "environments/$cloud/values.yaml" -f src/app/chart/versions.yaml `
    --application "applications/$cloud.yaml"
if ($LASTEXITCODE -ne 0) { throw 'Deployment input validation failed' }

$application = (& .venv/Scripts/python.exe -c "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))" "applications/$cloud.yaml") | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Application could not be read' }
helm dependency build ./src/app/chart
if ($LASTEXITCODE -ne 0) { throw 'Helm dependency build failed' }
helm lint ./src/app/chart --with-subcharts -f "environments/$cloud/values.yaml" -f src/app/chart/versions.yaml
if ($LASTEXITCODE -ne 0) { throw 'Helm lint failed' }
New-Item -ItemType Directory -Force .validation-output | Out-Null
helm template $application.spec.source.helm.releaseName ./src/app/chart `
    --namespace $application.spec.destination.namespace `
    -f "environments/$cloud/values.yaml" -f src/app/chart/versions.yaml > ".validation-output/$cloud.yaml"
if ($LASTEXITCODE -ne 0) { throw 'Helm render failed' }
.venv/Scripts/python.exe ci/check_rendered.py ".validation-output/$cloud.yaml" --environment $cloud
if ($LASTEXITCODE -ne 0) { throw 'Rendered Kubernetes type validation failed' }
```

빈칸, 시험 파일·주소, 잘못된 타입, 환경에 맞지 않는 이미지 경로, 다른 values를 읽는 Application은 실패해야 한다. actual 파일이 아직 없는 현재 준비 상태에서도 실패가 정상이다. 통과는 로컬 입력 계약과 템플릿의 확인이며 Secret 존재, registry pull, 실제 연결·기동·구매 성공의 증거는 아니다.

## 3. 기존 배포와 대상 확인

다음은 읽기 명령이다. `RETAIL_KUBE_CONTEXT`는 앱이 실행될 실제 클러스터 context, `RETAIL_ARGO_KUBE_CONTEXT`는 Argo CD가 설치된 클러스터 context, `RETAIL_ARGOCD_SERVER`는 로그인한 실제 Argo 서버로 설정되어 있어야 한다. 기본 context에 의존하지 않는다. Argo 서버는 검토한 인증 방식으로 먼저 로그인하고 TLS 검증을 유지한다.

```powershell
$targetContext = $env:RETAIL_KUBE_CONTEXT
$argoContext = $env:RETAIL_ARGO_KUBE_CONTEXT
$argoServer = $env:RETAIL_ARGOCD_SERVER
if (-not $targetContext -or -not $argoContext -or -not $argoServer) {
    throw 'Set the three confirmed Retail Store context/server environment variables first'
}
$appName = $application.metadata.name
$appNamespace = $application.spec.destination.namespace
$argoNamespace = $application.metadata.namespace

kubectl config get-contexts
kubectl --context $argoContext get applications.argoproj.io -A
helm list --kube-context $targetContext --all-namespaces
kubectl --context $targetContext -n $appNamespace get deployment,service,ingress,serviceaccount
argocd --server $argoServer cluster list
argocd --server $argoServer repo list
argocd --server $argoServer proj get $application.spec.project
```

EKS/GKE 정체성, Argo에 등록된 destination 이름/주소, namespace, 기존 releaseName과 Application 소유권을 대조한다. 기존 앱이 다른 이름으로 등록되어 있으면 새 Application으로 중복 관리하지 말고 해당 정의를 이어받는 방법을 확인한다. releaseName이나 Application 이름 변경은 selector·추적 라벨에 영향을 줄 수 있으므로 실제 diff에서 확인한다.

Argo가 없다면 Kubernetes/CD 담당이 기존 `bootstrap/argocd/values-aws.yaml`, chart 10.2.1 검증 결과와 실제 설치 요구사항을 대조해 설치한다. 설치 버전·namespace·접근 방식이 확정되기 전에는 이 문서에서 임의의 새 설치를 실행하지 않는다. Application에는 기존 AppProject의 source/destination 권한과 GitOps repo 읽기 권한, 실제 클러스터 등록이 필요하다.

대상 namespace의 Secret 이름과 키 이름을 확인한다. 다음 명령은 완료한 values에서 Orders Secret 참조를 읽고 Secret의 데이터 키 이름만 출력한다.

```powershell
$ordersSecret = & .venv/Scripts/python.exe -c "import sys,yaml; v=yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(v['orders']['app']['persistence']['secret']['name'])" "environments/$cloud/values.yaml"
if ($LASTEXITCODE -ne 0 -or -not $ordersSecret) { throw 'Orders Secret reference is missing' }
kubectl --context $targetContext -n $appNamespace get secret $ordersSecret -o 'go-template={{range $key, $value := .data}}{{printf "%s\n" $key}}{{end}}'
```

WhaTap을 사용하는 경우 동일한 방식으로 실제 `secretName`의 `license` 키를 확인한다. DB/Redis 네트워크와 인증, DynamoDB 권한·테이블은 관련 담당자와 대상 workload 경로에서 검증한다.

## 4. Application 등록과 수동 동기화

이 단계는 클러스터를 변경한다. 앞 단계가 완료되고 검토한 실제 파일이 targetRevision에 포함된 경우 실행한다.

기존 Application은 **소스·values 경로를 바꾸기 전에** 자동 동기화와 실행 중인 작업을 확인한다. 예제에서 `syncPolicy`를 생략하는 것만으로 live 정책이 꺼지지는 않는다. 다른 도구가 설정한 필드는 apply 후에도 남을 수 있다. [Kubernetes apply의 병합 동작](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/declarative-config/#merge-patch-calculation).

ApplicationSet 소유 객체라면 아래 직접 적용 절차를 중단하고 상위 ApplicationSet 템플릿의 소스·동기화 정책을 검토하는 변경으로 진행한다. 다른 GitOps 정의나 컨트롤러가 관리한다면 그 원본도 함께 조정해야 한다. 자식 Application만 바꾸면 다시 덮어써질 수 있다. 진행 중인 operation이 있으면 완료 또는 담당자의 명시적인 종료 조치 후 상태를 재확인한다.

단독 Application일 때는 다음처럼 수동 정책으로 바꾸고 **실제 live 상태가 수동·유휴인지** 확인한다. 이 확인이 끝나기 전에는 source 변경을 apply하지 않는다. `--sync-policy none`은 수동 정책의 공식 별칭이다. [Argo CD app set](https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_app_set/).

```powershell
$existingJson = kubectl --context $argoContext -n $argoNamespace get applications.argoproj.io $appName --ignore-not-found -o json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the existing Application' }
if ($existingJson) {
    $existingApp = $existingJson | ConvertFrom-Json
    if (@($existingApp.metadata.ownerReferences | Where-Object { $_.kind -eq 'ApplicationSet' }).Count -gt 0) {
        throw 'Update the owning ApplicationSet; do not apply a standalone child definition'
    }
    if ($null -ne $existingApp.operation -or $existingApp.status.operationState.phase -in @('Running', 'Terminating')) {
        throw 'Resolve the active Application operation before changing the source'
    }
    argocd --server $argoServer app set $appName --app-namespace $argoNamespace --sync-policy none
    if ($LASTEXITCODE -ne 0) { throw 'Could not disable existing automated sync' }
    $pausedJson = kubectl --context $argoContext -n $argoNamespace get applications.argoproj.io $appName -o json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify the paused Application' }
    $pausedApp = $pausedJson | ConvertFrom-Json
    $autoPolicy = $pausedApp.spec.syncPolicy.automated
    if ($null -ne $autoPolicy -and $autoPolicy.enabled -ne $false) {
        throw 'Live automated sync is still enabled; do not change the source'
    }
    if ($null -ne $pausedApp.operation -or $pausedApp.status.operationState.phase -in @('Running', 'Terminating')) {
        throw 'An operation is active; do not change the source'
    }
}
```

신규 Application과 수동·유휴 상태를 확인한 단독 Application만 아래로 진행한다. 예제의 빈칸을 채운 후에도 최초 적용 전 server dry-run과 기존 객체 diff를 확인한다.

```powershell
kubectl --context $argoContext apply --dry-run=server -f "applications/$cloud.yaml"
if ($LASTEXITCODE -ne 0) { throw 'Application server validation failed' }
kubectl --context $argoContext diff -f "applications/$cloud.yaml"
```

`kubectl diff`의 종료 코드 1은 차이가 있다는 뜻이며, 차이 내용을 검토해야 한다. 코드 0은 차이가 없음, 그보다 큰 값은 오류다. 검토 후 등록한다. [Kubernetes 공식 diff 문서](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_diff/).

```powershell
kubectl --context $argoContext apply -f "applications/$cloud.yaml"
if ($LASTEXITCODE -ne 0) { throw 'Application apply failed' }
$appliedJson = kubectl --context $argoContext -n $argoNamespace get applications.argoproj.io $appName -o json
if ($LASTEXITCODE -ne 0) { throw 'Cannot verify the applied Application' }
$appliedApp = $appliedJson | ConvertFrom-Json
$autoPolicy = $appliedApp.spec.syncPolicy.automated
if ($null -ne $autoPolicy -and $autoPolicy.enabled -ne $false) { throw 'Automated sync was re-enabled; inspect its owner before continuing' }
if ($null -ne $appliedApp.operation -or $appliedApp.status.operationState.phase -in @('Running', 'Terminating')) { throw 'Unexpected operation after apply; inspect before continuing' }
argocd --server $argoServer app get $appName --refresh
argocd --server $argoServer app diff $appName
```

Argo manifest 생성에 실패하면 경로·로컬 chart dependency·values·repo 권한과 실제 repo-server Helm 버전을 확인한다. diff에서 대상 namespace, 이미지 버전, IAM 참조, Ingress와 삭제 예정 리소스를 검토한다. 최초 초안에는 자동 sync와 자동 prune이 없으며 `--prune`, 강제 적용, namespace 자동 생성 옵션을 붙이지 않는다. 정책은 운영 합의에 따라 별도 변경한다. [Argo CD 자동 sync/prune 문서](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/).

```powershell
argocd --server $argoServer app sync $appName
if ($LASTEXITCODE -ne 0) { throw 'Argo CD sync failed' }
argocd --server $argoServer app wait $appName --sync --health --timeout 300
if ($LASTEXITCODE -ne 0) { throw 'Argo CD did not become Synced and Healthy' }
kubectl --context $targetContext -n $appNamespace get pods -o 'custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,IMAGE:.spec.containers[*].image,IMAGE_ID:.status.containerStatuses[*].imageID'
```

## 5. 서비스 확인과 실패 대응

Argo sync revision과 Pod의 실제 imageID를 릴리스 인계 기록과 대조한다. `Synced/Healthy`나 health 200만으로 구매 성공을 판정하지 않는다. 확인한 앱 주소에서 상품 조회 → 장바구니 추가 → 결제·주문 생성 → 주문 조회까지 실행하고 시간·대상 commit·이미지·주문 식별자·결과를 기록한다. 재시작이나 Pod 교체 후 세션·장바구니 유지가 요구되는 경우도 시험한다.

| 증상 | 우선 확인 |
|---|---|
| ComparisonError | repo 접근, Application path/valueFiles, Helm dependency, schema 오류 |
| ImagePullBackOff | 실제 게시 tag/digest와 대상 노드·workload의 registry pull 권한 |
| CrashLoopBackOff | 앱 로그, Catalog WhaTap 필수 입력, Secret 참조, DB/Redis 연결 |
| Pending | 스케줄링 이벤트, 리소스·노드 수, 현재 차트의 topologySpreadConstraints |
| Healthy지만 구매 실패 | UI→Catalog/Cart/Checkout/Orders와 Redis/Postgres/DynamoDB 각 연결 |
| TLS Redis 연결 실패 | UI TLS 지원 여부. 요구사항을 끄는 방식으로 우회하지 않음 |

읽기용 진단 명령은 명시한 대상 context/namespace에서 실행한다.

```powershell
kubectl --context $targetContext -n $appNamespace get events --sort-by=.lastTimestamp
kubectl --context $targetContext -n $appNamespace get deployment,pods,service,ingress
argocd --server $argoServer app get $appName --refresh
```

## 6. Git을 통한 롤백

목표는 이전에 검증한 앱과 설정으로 돌아가는 것이다. 실패한 배포의 GitOps commit, 직전 정상 commit과 이미지, DB/데이터 호환성을 먼저 확인한다. 이미지·차트 되돌리기는 DB 스키마나 데이터 복구를 수행하지 않는다.

클린한 GitOps checkout에서 실패한 **단일 일반 commit**을 되돌릴 경우 다음처럼 복구 PR을 준비한다. `RETAIL_BAD_CD_COMMIT`에는 확인한 전체 commit SHA를 지정한다. merge commit은 mainline 선택이 필요하므로 이 명령을 그대로 사용하지 않는다.

```powershell
$badCdCommit = $env:RETAIL_BAD_CD_COMMIT
if ($badCdCommit -notmatch '^[0-9a-f]{40}$') { throw 'Set the reviewed GitOps commit SHA' }
git status --short
git show --stat $badCdCommit
# 위 결과에서 작업 디렉터리와 되돌릴 변경 범위를 확인한 다음 실행한다.
git switch -c codex/rollback-retail-release
git revert $badCdCommit
```

복구 변경에 구조·배포 입력 검사와 Helm 렌더링을 다시 실행하고 PR로 검토·병합한다. Application이 복구 commit을 읽는지 확인한 후 4단계의 diff → 수동 sync → wait, 5단계의 실제 구매·주문 검증을 반복한다. 직접 `kubectl set image`만 바꾸는 방식은 Git의 원하는 상태와 달라지므로 정상 복구 절차로 삼지 않는다.

두 환경이 공통 `versions.yaml`을 읽으므로 버전 rollback의 영향도 AWS/GCP 모두에 걸친다. 각 환경의 실제 sync 시점과 현재 서비스 상태를 확인한다. 시험 결과에는 복구 소요 시간, 되돌린 commit·이미지, 구매 결과, 남은 데이터 영향까지 적는다.
