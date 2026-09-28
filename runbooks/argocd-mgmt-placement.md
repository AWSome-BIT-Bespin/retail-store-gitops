# AWS Argo CD — MGMT 배치

## 변경 범위

Argo CD를 관리용 노드에 배치한다. 차트 `10.9.2`, Argo CD `v3.5.3`,
ClusterIP·Ingress 비활성·비 HA Redis 설정은 유지한다.

```yaml
global:
  nodeSelector:
    kubernetes.io/os: linux
    workload: mgmt
  tolerations:
    - key: dedicated
      operator: Equal
      value: mgmt
      effect: NoSchedule
```

- `nodeSelector`: `workload=mgmt`인 Linux 노드만 선택한다.
- `tolerations`: 해당 노드의 `dedicated=mgmt:NoSchedule` 테인트를 허용한다.
  이 설정 자체가 노드를 기동하거나 수량을 늘리지는 않는다.
- 위 조건은 application-controller, applicationset-controller, dex-server,
  notifications-controller, redis, repo-server, server에 적용된다.
  신규 설치 때 실행하는 Redis 초기화 Job에도 차트의 전역 배치 조건이 전달된다.
- Application 등록·Sync, 이미지 변경, 애드온 자동화, Terraform·GCP 변경은 포함하지 않는다.

## 설정 파일과 설치 경로

- [`bootstrap/argocd/values-aws.yaml`](../bootstrap/argocd/values-aws.yaml):
  Helm 설정의 기준 파일이며 기존 CI에서 lint·render한다.
- [`bootstrap/argocd/ssm-install-aws.yaml`](../bootstrap/argocd/ssm-install-aws.yaml):
  최초 설치용 SSM 문서 소스. 같은 값을 고정된 Helm 인자로 전달한다.
  기존 Argo namespace·CRD·release가 있으면 중단하는 보호 조건은 유지한다.
- [`ci/test_argocd_bootstrap.py`](../ci/test_argocd_bootstrap.py):
  MGMT 조건, 기존 접근 설정, SSM 인자와 values의 일치를 오프라인으로 검사한다.

**GitHub의 파일을 병합해도 AWS에 이미 등록된 SSM 문서는 자동으로 바뀌지 않는다.**
이번 변경에서 AWS 문서·IAM·GitHub 변수나 워크플로 실행은 변경하지 않는다.
설치 워크플로의 `ARGO_DOCUMENT_VERSION`은 여전히 `1`이며
`ARGOCD_BOOTSTRAP_DOCUMENT_SHA256` 변수로 등록 문서의 해시를 고정한다.

향후 신규 설치 경로에 이 내용을 활성화할 때는 별도 승인을 받은 뒤 다음을 함께 처리한다.

1. 대상 Bastion·EKS·VPC 식별자와 MGMT 노드 라벨·테인트·Ready 상태를 재확인한다.
2. 검토된 SSM 소스를 AWS의 새 문서 버전으로 등록하고 반환된 버전·SHA-256을 확인한다.
3. 워크플로의 문서 버전과 GitHub의 해시 변수를 그 동일한 버전에 맞춘다.
4. Argo CD가 없는 신규 설치 대상에서만 승인 후 수동 설치 워크플로를 실행한다.

기존 클러스터의 MGMT 배치를 바꾸기 위해 최초 설치 워크플로를 다시 실행하지 않는다.
현재 CoreDNS·metrics-server는 MGMT 테인트를 허용하지 않는 것으로 조회됐으므로,
이 조건을 별도 변경하지 않는 한 해당 구성요소를 위한 APP 노드 가동도 필요하다.

## 2026-09-29 적용 기록 — 송진하

아래는 이 PR을 작성하기 **전에 별도 승인받아 수행한** 실제 작업 기록이다.
이 PR의 생성·병합이 아래 작업을 자동 실행한다는 뜻은 아니다.

- 기존 AWS `argocd` release의 배치 조건만 변경했다. 차트·앱 버전은 유지하고
  기존 Helm values를 재사용했으며, 기존 설치의 Redis 초기화 hook은 실행하지 않았다.
- Helm revision `1 → 2`. MGMT 노드에서 Argo Pod 7개 Ready와 내부 loopback
  HTTPS `/healthz` 응답 `200`을 확인했다. 사용자 웹 로그인·쇼핑몰 기능은 검증하지 않았다.
- 검증 후 임시로 실행한 MGMT 2대·APP 1대를 종료했다. 최종 조회에서 두 그룹의
  최소·최대·목표 수량은 모두 `0/0/0`, Kubernetes 노드 0개, Bastion은 기존 running 상태였다.
- AWS SSM 기록 기준 `02:50:27~02:50:33 KST` 최종 조회에서 revision 2와 MGMT 배치 설정을
  보존한 채 Argo Pod 7개는 Pending이었다. 노드가 0개이면 관리 화면도 가동하지 않는다.
- Application은 0개이며 등록·Sync를 수행하지 않았다. 종료 훅 우회·강제 종료도 하지 않았다.

## 변경 검증

실행 위치: 저장소 루트, Python 환경에 `ci/requirements.txt` 설치, Helm `v4.2.3` 준비.
아래 명령은 로컬 검사이며 Kubernetes에 설치하지 않는다.

```bash
python -B -m unittest discover -s ci -p 'test_*.py' -v
```

차트 `10.9.2`로 values 파일과 SSM 고정 인자를 각각 렌더링하여 MGMT 조건이 같은지도 비교한다.
CI 통과는 신규 설치나 현재 클러스터의 가동을 보증하지 않는다.
