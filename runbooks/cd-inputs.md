# CD 필수 입력과 담당 영역

기준일: 2026-09-15. 저장소 main은 앱 `2e816a03793c1ba56f69eb996f483680164f4f02`, GitOps `8cb8812f7c5f3ddc4f4d2a424d62e27e9b9bbf96`까지 확인했다. 이 표의 역할은 협업 제안이며 담당자 이름이나 팀 합의로 확정한 내용이 아니다.

| 받아야 할 정보 | 값을 제공·확인할 영역 | CD 담당이 할 일 | 현재 상태 |
|---|---|---|---|
| EKS/GKE 클러스터 ID, 접근 context, 리전·프로젝트 | 인프라·Kubernetes | 실제 target과 Argo 등록명을 대조 | 미확인 |
| Argo 설치 위치·버전·접속 서버·AppProject | Kubernetes·CD | 기존 설치와 Application 유무 확인 | bootstrap values만 존재 |
| 기존 Application 이름, namespace, Helm releaseName, 리소스 소유권 | Kubernetes·CD | 중복 생성 없이 기존 배포와 연결 | 미확인 |
| GitOps repo 읽기 권한과 ECR/GAR image pull 권한 | 인프라·Kubernetes·CD | 권한별 실제 pull/manifest 생성 검증 | 클러스터에서 미검증 |
| AWS Cart DynamoDB tableName, IRSA 역할·권한·신뢰 관계 | 인프라·앱 | values에 실제 이름과 ARN 연결 | 예제 빈칸. 실제 리소스 미확인 |
| Checkout/UI Redis 주소, 포트, TLS·인증 요구사항 | 인프라·앱 | 올바른 차트 입력과 연결 가능성 검증 | 최종 값 미확인 |
| Orders Postgres 주소·포트·DB 이름, 기존 Secret 이름 | 인프라·DB·앱 | 참조와 실제 읽기·쓰기 확인 | 최종 값 미확인 |
| GCP Cart 저장 방식, DB 복제·전환·Redis·세션 처리 | 앱·DB·인프라·DR | AWS 장애 시 사용할 수 있는 입력과 시험 절차 연결 | 미결정; 임의로 인메모리 확정 금지 |
| AWS/GCP 트래픽 진입, DNS, TLS, Ingress controller·class | 인프라·Kubernetes | 환경별 ingress와 접근 확인 | AWS ALB 설정만 관찰; 실제 구성 미확인 |
| Catalog/Checkout WhaTap 사용 여부, Secret 이름, 수집 서버 | 앱·관측 | 선택한 이미지와 차트의 기동 조건 확인 | Catalog 새 이미지 불일치 확인 |
| 각 서비스 게시 버전·소스 SHA·CI run·ECR/GAR digest | CI·앱 | 증거 확인 후 GitOps 버전 변경 | 기존 기록만 있음; 새 후보 미게시 |
| 동기화·삭제 정책, 검토 담당, 장애 시 복구 기준 | CD·Kubernetes·프로젝트 리드 | 정책을 명시하고 실제 시험 | 수동 sync 초안; 팀 확정 아님 |

## 값 전달 형식

GitOps 저장소의 `environments/<cloud>/values.example.yaml`을 입력 양식으로 쓴다. 실제 입력이 확인되면 같은 디렉터리의 `values.yaml`로 만들고 PR에서 검토한다. Application 예제도 같은 방식으로 `applications/<cloud>.yaml`로 만든다. 두 실제 파일이 배포 입력 검증 대상이다.

비밀번호·라이선스·클라우드 키를 values나 PR에 넣지 않는다. 기존 Secret 이름과 필요한 키 이름만 전달한다. Orders의 현재 템플릿이 읽는 키는 다음과 같다.

```text
RETAIL_ORDERS_PERSISTENCE_USERNAME
RETAIL_ORDERS_PERSISTENCE_PASSWORD
```

Catalog/Checkout WhaTap Secret의 키는 다음과 같다.

```text
license
```

예제는 현재 AWS Cart의 IRSA 방식에 맞춰져 있다. EKS Pod Identity나 외부 ServiceAccount를 쓰는 것으로 결정된다면 이 예제·검증 규칙을 그 실제 방식에 맞춰 변경한 후 사용한다.

## 현재 확인된 차단 요인

**Catalog:** 9월 14일 현재 앱 main으로 빌드한 로컬 이미지에서 WhaTap 환경변수가 없으면 `WHATAP_LICENSE is required`로 종료됐다. 현재 GitOps 버전은 Catalog v0.0.2이고 새 v0.0.3은 로컬 후보다. 새 이미지 게시·버전 전환 전에 WhaTap을 켜서 필요한 참조·볼륨을 제공할지, 계측을 끈 기동을 지원할지 앱·관측 담당과 결정하고 재시험한다. 오프라인 입력 검사는 이 기동 시험을 대신하지 않는다.

**UI Redis TLS:** 현재 `src/ui/chart/templates/configmap.yml`은 URL을 `redis://`로 생성하며 `ui.app.session.redis.tls`를 읽지 않는다. 따라서 TLS가 필요한 Redis를 사용하려면 해당 지원을 별도 수정·검증해야 한다. CD 검사에서는 TLS true를 오류로 처리하며, 요구사항을 false로 바꿔서 우회하면 안 된다. Redis 인증·ACL이 필요하다면 현재 차트의 인증 전달 지원도 먼저 확인한다. Checkout은 TLS 값에 따라 `redis://` 또는 `rediss://`를 생성한다.

**GCP:** GAR 경로가 준비되어도 Cart 데이터·Orders 데이터 복제·Redis·세션·트래픽 전환이 자동 완성되지 않는다. GCP 예제는 Cart provider를 비워 둔다. 시험 fixture의 인메모리 선택은 DR 완료 근거가 아니다. 오프라인 검사는 알려진 AWS 주소·ECR·IRSA·ALB 설정의 혼입을 잡을 수 있지만 사설 DNS, 라우팅, 복제 상태와 실제 장애 독립성은 실환경에서 검증해야 한다.

**접근·실행 버전:** 현재 작업에서는 EKS/GKE나 Argo 런타임에 접속해 확인하지 않았다. CI Helm은 4.2.3이며 bootstrap 검증은 argo-cd chart 10.2.1을 사용한다. 실제 Argo 설치 버전과 repo-server의 Helm 렌더링 결과는 확인 후 대조한다.

## 담당 완료 기준

CD 준비 완료는 입력 양식·검증·Application 초안·인계 및 복구 절차가 검토 가능한 상태라는 뜻이다. CD 실행 완료에는 게시 증거가 있는 버전으로 실제 Argo manifest 생성·동기화·health·구매·주문 조회·이전 버전 복귀를 검증한 기록이 추가로 필요하다. GCP DR 성공과 DB 복구 목표 충족은 관련 담당자와 별도 증거로 확인한다.
