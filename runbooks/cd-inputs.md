# CD 필수 입력과 담당 영역

기준일: 2026-09-16. 앱 `14b3fb3726f20780b7591155bae9154d90394899`의 새 이미지 게시와 AWS 실환경을 직접 확인했다. 이번 변경은 GitOps `fbb6480199756a1fc01200adeeef63bea3ce3761`에서 이어진다. 아래 담당 영역은 협업 제안이며 팀 담당자 합의를 뜻하지 않는다.

| 받아야 할 정보 | 값을 제공·확인할 영역 | CD 담당이 할 일 | 현재 상태 |
|---|---|---|---|
| EKS/GKE 클러스터 ID, 접근 context, 리전·프로젝트 | 인프라·Kubernetes | 실제 target과 Argo 등록명을 대조 | AWS 350606136784 / ap-northeast-2 / retail-dev-eks 확인. GKE 미확인 |
| Argo 설치 위치·버전·접속 서버·AppProject | Kubernetes·CD | 기존 설치와 Application 유무 확인 | argocd namespace, v3.4.5, default 프로젝트 확인 |
| 기존 Application 이름, namespace, Helm releaseName, 리소스 소유권 | Kubernetes·CD | 중복 생성 없이 기존 배포와 연결 | 시작 시 Application 0개, Helm retail-dev revision 4, namespace retail-dev |
| GitOps repo 읽기 권한과 ECR/GAR image pull 권한 | 인프라·Kubernetes·CD | 권한별 실제 pull/manifest 생성 검증 | 클러스터에서 미검증 |
| AWS Cart DynamoDB tableName, IRSA 역할·권한·신뢰 관계 | 인프라·앱 | values에 실제 이름과 ARN 연결 | retail-dev-cart 테이블·ServiceAccount와 RetailDevCartDynamoDBRole 확인. 기존 table-name Secret 참조 보존 |
| Checkout/UI Redis 주소, 포트, TLS·인증 요구사항 | 인프라·앱 | 올바른 차트 입력과 연결 가능성 검증 | 공통 retail-dev-valkey:6379, 현재 TLS false 확인. 실제 hostname은 AWS values에 기록 |
| Orders Postgres 주소·포트·DB 이름, 기존 Secret 이름 | 인프라·DB·앱 | 참조와 실제 읽기·쓰기 확인 | retail-dev-orders:5432 / orders / orders-db 확인 |
| GCP Cart 저장 방식, DB 복제·전환·Redis·세션 처리 | 앱·DB·인프라·DR | AWS 장애 시 사용할 수 있는 입력과 시험 절차 연결 | 미결정; 임의로 인메모리 확정 금지 |
| AWS/GCP 트래픽 진입, DNS, TLS, Ingress controller·class | 인프라·Kubernetes | 환경별 ingress와 접근 확인 | AWS ui Ingress / alb class / retail-dev-alb와 기존 HTTP 200 확인. GCP 미확인 |
| Catalog/Checkout WhaTap 사용 여부, Secret 이름, 수집 서버 | 앱·관측 | 선택한 이미지와 차트의 기동 조건 확인 | 두 서비스 enabled, whatap-credentials/license, 별도 host 환경변수 확인. 실제 telemetry 수집은 별도 검증 |
| 각 서비스 게시 버전·소스 SHA·CI run·ECR/GAR digest | CI·앱 | 증거 확인 후 GitOps 버전 변경 | 새 Catalog v0.0.3 / Checkout v0.0.4 게시 확인. 릴리스 인계 문서 참조 |
| 동기화·삭제 정책, 검토 담당, 장애 시 복구 기준 | CD·Kubernetes·프로젝트 리드 | 정책을 명시하고 실제 시험 | 수동 sync 초안; 팀 확정 아님 |

## 값 전달 형식

GitOps 저장소의 `environments/<cloud>/values.example.yaml`을 입력 양식으로 쓴다. 실제 입력이 확인되면 같은 디렉터리의 `values.yaml`로 만들고 PR에서 검토한다. Application 예제도 같은 방식으로 `applications/<cloud>.yaml`로 만든다. 두 실제 파일이 배포 입력 검증 대상이다.

비밀번호·라이선스·클라우드 키를 values나 PR에 넣지 않는다. 기존 Secret 이름과 필요한 키 이름만 전달한다. Orders의 현재 템플릿이 읽는 키는 다음과 같다.

```text
RETAIL_ORDERS_PERSISTENCE_USERNAME
RETAIL_ORDERS_PERSISTENCE_PASSWORD
```

이번 AWS 환경에서 Catalog/Checkout WhaTap Secret의 키는 다음과 같다. 수집 서버는 `whatap.serverHost`로 별도 전달한다. Checkout에서 이 값을 비우면 같은 Secret의 `serverHost` 키를 사용하는 기존 방식이 유지되므로, 그 경우에는 두 키 모두 존재해야 한다.

```text
license
```

예제는 현재 AWS Cart의 IRSA 방식에 맞춰져 있다. EKS Pod Identity나 외부 ServiceAccount를 쓰는 것으로 결정된다면 이 예제·검증 규칙을 그 실제 방식에 맞춰 변경한 후 사용한다.

## 현재 확인된 차단 요인

**Catalog/Checkout:** 9월 14일의 Catalog 기동 실패는 당시 소스의 기록이다. 9월 16일 새 소스와 정식 이미지가 게시됐으며 이번 AWS 설정은 현재 사용 중인 WhaTap 활성화·Secret·수집 서버·볼륨을 유지한다. Catalog의 잘못된 `valueFrom` 형식을 고치고 Checkout은 별도 수집 서버 값을 지원한다. 새 이미지의 실제 기동과 수집 성공은 배포 결과에서 확인한다.

**UI Redis TLS:** endpoint로 ConfigMap URL을 생성하는 경로는 `redis://`로 고정되어 있다. `secretName` 경로는 Secret의 `url` 전체를 주입하므로 두 경로를 구분해야 한다. 이번 AWS 배포는 직접 확인한 기존 endpoint·TLS false 설정을 유지한다. TLS/인증이 필요한 다른 환경에서는 Secret URL 경로와 실제 연결을 검증하고 배포 입력 검사도 그 구성에 맞춰 조정한다. Checkout은 endpoint 경로에서도 TLS 값에 따라 URL scheme을 선택한다.

**GCP:** GAR 경로가 준비되어도 Cart 데이터·Orders 데이터 복제·Redis·세션·트래픽 전환이 자동 완성되지 않는다. GCP 예제는 Cart provider를 비워 둔다. 시험 fixture의 인메모리 선택은 DR 완료 근거가 아니다. 오프라인 검사는 알려진 AWS 주소·ECR·IRSA·ALB 설정의 혼입을 잡을 수 있지만 사설 DNS, 라우팅, 복제 상태와 실제 장애 독립성은 실환경에서 검증해야 한다.

**접근·실행 버전:** EKS 1.35와 Argo CD v3.4.5를 실제 조회했다. 로컬 AWS 계정의 직접 kubectl 접근 대신 기존 Bastion의 ec2-user와 프로젝트 kubeconfig로 접근하며 접근 권한을 확대하지 않았다. CI Helm은 4.2.3, bootstrap 검증 chart는 10.2.1이다. 기존 Argo 설치를 재설치하지 않으며 repo-server 렌더링은 Application 연결 시 확인한다.

## 담당 완료 기준

CD 준비 완료는 입력 양식·검증·Application 초안·인계 및 복구 절차가 검토 가능한 상태라는 뜻이다. CD 실행 완료에는 게시 증거가 있는 버전으로 실제 Argo manifest 생성·동기화·health·구매·주문 조회·이전 버전 복귀를 검증한 기록이 추가로 필요하다. GCP DR 성공과 DB 복구 목표 충족은 관련 담당자와 별도 증거로 확인한다.
