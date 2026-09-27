# AWS 애플리케이션 NetworkPolicy

각 앱 Helm 차트가 자기 Pod의 수신 통신(Ingress)을 제한하는 정책을 관리한다.

| 앱 | 정책 파일 |
|---|---|
| UI | `src/ui/chart/templates/networkpolicy-ui.yaml` |
| Carts | `src/cart/chart/templates/networkpolicy-carts.yaml` |
| Catalog | `src/catalog/chart/templates/networkpolicy-catalog.yaml` |
| Checkout | `src/checkout/chart/templates/networkpolicy-checkout.yaml` |
| Orders | `src/orders/chart/templates/networkpolicy-orders.yaml` |

각 앱의 `values.yaml`에 `networkPolicy` 기본값을 두고,
AWS 환경 파일 `environments/aws/values.yaml`에서 `ui.networkPolicy.enabled`,
`cart.networkPolicy.enabled`, `catalog.networkPolicy.enabled`,
`checkout.networkPolicy.enabled`, `orders.networkPolicy.enabled`를 `true`로 설정한다.
앱별 기본값은 `false`이므로 현재 GCP values에는 정책이 추가되지 않는다.
상위 차트 최상단의 `networkPolicy.enabled`는 더 이상 사용하지 않는다.

## 허용 범위

| 대상 Pod | 허용 출발지 | 대상 포트 |
|---|---|---|
| UI | 모든 출발지. AWS values의 `ui.networkPolicy.ingressCidrs`를 지정하면 해당 CIDR과 모니터링 네임스페이스로 제한 | TCP `http` (현재 8080) |
| Carts | 같은 네임스페이스/릴리스의 UI, 모니터링 네임스페이스 | TCP `http` (현재 8080) |
| Catalog | 같은 네임스페이스/릴리스의 UI, 모니터링 네임스페이스 | TCP `http` (현재 8080) |
| Checkout | 같은 네임스페이스/릴리스의 UI, 모니터링 네임스페이스 | TCP `http` (현재 8080) |
| Orders | 같은 네임스페이스/릴리스의 UI와 Checkout, 모니터링 네임스페이스 | TCP `http` (현재 8080) |

대상 Pod 라벨은 해당 앱 차트의 `selectorLabels`를 재사용하므로,
그 앱의 릴리스 이름과 `nameOverride`를 반영한다.
허용할 호출 앱은 각 백엔드의 `networkPolicy.allowedClients` 목록으로 설정한다.
`name`은 호출 Pod의 `app.kubernetes.io/name` 라벨이고,
`releaseName`은 `app.kubernetes.io/instance` 라벨이다.
`releaseName: ""` 또는 생략은 대상 앱과 같은 릴리스 이름을 사용한다.
호출 앱의 `nameOverride`를 변경했다면 이 목록의 `name`도 실제 라벨에 맞춰 변경한다.
같은 네임스페이스에서 앱을 별도 릴리스로 설치할 때는 호출 앱의 `releaseName`을 명시한다.
출발지에도 `component: service`, `owner: retail-store-sample` 라벨을 함께 요구한다.

예를 들어 AWS 환경 파일에서 Orders의 허용 앱은 다음처럼 지정할 수 있다.
목록을 덮어쓸 때는 허용할 앱 전체를 적는다.

```yaml
orders:
  networkPolicy:
    enabled: true
    allowedClients:
      - name: ui
        releaseName: ""  # 현재 상위 차트와 함께 배포하므로 같은 릴리스
      - name: checkout
        releaseName: ""
    monitoring:
      namespace: whatap-monitoring
```

앱 차트를 단독으로 사용할 때는 해당 앱 `values.yaml`의 최상단 `networkPolicy`를 설정한다.
다른 앱 차트의 helper나 상위 차트의 값을 직접 참조하지 않는다.
백엔드에서 `allowedClients: []`와 빈 모니터링 네임스페이스를 함께 지정하면
`ingress: []`로 수신을 차단하며, 전체 출발지 허용으로 바뀌지 않는다.

AWS의 모니터링 네임스페이스는 인프라 설치 스크립트에 맞춘 `whatap-monitoring`이다.
앱별 `networkPolicy.monitoring.namespace: ""`이면 별도의 모니터링 허용 규칙을 만들지 않는다.
네임스페이스를 지정하면 그 안의 모든 Pod가 앱 HTTP 포트에 접근할 수 있다.
이 규칙은 메트릭 URL만 허용하는 규칙이 아니며, NetworkPolicy는 HTTP 경로를 구분하지 않는다.

이번 정책은 `policyTypes: [Ingress]`만 사용한다. DNS, 외부 RDS/Redis/DynamoDB,
Pod Identity 자격증명과 WhaTap 전송 등 Egress를 추가로 제한하지 않는다.
이미 존재하는 다른 Egress 정책을 해제하는 효과는 없다.
네임스페이스 전체를 선택하는 기본 차단 정책도 추가하지 않는다.

## UI와 ALB

현재 AWS values의 `ui.networkPolicy.ingressCidrs: []`는 모든 출발지의 TCP `http` 접근을 허용한다.
ALB만을 허용하는 정책은 아니다. 실제 ALB가 사용하는 출발지 서브넷 CIDR을 확인한 뒤
AWS values에 입력하면 범위를 좁힐 수 있다. 변경할 때는 ALB 대상 상태와 헬스 체크도 확인한다.
AWS Load Balancer Controller Pod를 허용하는 것으로 ALB 데이터 트래픽을 허용할 수는 없다.

## 배포 연결과 전제 조건

`applications/aws.yaml`의 기존 차트/values 경로를 그대로 사용한다.
상위 차트는 다섯 앱 차트를 의존성으로 포함하므로 앱별 정책도 함께 배포한다.
로컬에서 이미 하위 차트를 패키징했다면 배포 준비 시 기존 Helm dependency build 절차로
수정한 하위 차트를 다시 포함해야 한다. 이번 변경에서는 이 명령을 실행하지 않았다.
정책의 리소스 이름(`<릴리스>-ui`, `<릴리스>-carts` 등)과 네임스페이스는 유지한다.
정책에는 `argocd.argoproj.io/sync-wave: "-1"`을 지정하여,
Argo CD Sync에서 기본 wave의 애플리케이션 리소스보다 먼저 적용하도록 한다.
직접 Helm 배포에서는 Argo CD의 wave 순서가 적용되지 않는다.
현재 Application은 `main` 브랜치를 수동 Sync하므로 해당 브랜치에 변경이 반영되어야 한다.

EKS의 VPC CNI NetworkPolicy 기능은 별도로 활성화되어 있어야 한다.
이 차트는 NetworkPolicy 리소스를 생성하며 CNI 애드온 설정을 바꾸지 않는다.
CNI의 standard 모드에서는 새 Pod의 정책 반영 전 기본 허용 구간이 있을 수 있으므로,
Sync 순서만으로 Pod 시작 순간부터 차단된다고 보장하지 않는다.

정책 포트는 기존 Service 및 컨테이너 포트의 이름과 동일한 `http`를 사용한다.
현재 차트의 Service 포트는 80, Pod 포트는 8080이다. AWS 공식 문서에는
Service/컨테이너 포트 일치와 named port 이름 일치에 관한 제약이 있으므로,
배포 전 사용 중인 VPC CNI 버전의 제약을 확인하고 실제 Service 경유 허용/차단을 확인해야 한다.
named port 사용만으로 모든 CNI 버전에서 이 포트 변환이 검증된 것은 아니다.

기존 NetworkPolicy의 허용 규칙은 합쳐진다. 같은 Pod에 이미 전체 허용 정책이 있으면
이번 정책을 추가해도 그 허용 범위가 좁아지지 않는다.
호스트/노드 통신에도 일반 Pod 통신과 다른 예외가 있다.

## 정책 해제

AWS values에서 해당 앱의 `<앱>.networkPolicy.enabled`를 `false`로 변경하면
그 앱 차트가 정책을 생성하지 않는다. 전체 해제는 다섯 앱 모두를 비활성화한다.
현재 Argo CD Application은 자동 prune을 사용하지 않으므로,
이미 배포한 정책은 해당 다섯 리소스만 선택해 삭제하거나 선택적으로 prune해야 한다.
다른 리소스를 포함하는 일괄 prune은 필요하지 않다.

## 검증 상태와 참고 문서

이번 작성에서는 요청에 따라 Helm 렌더링 검증과 클러스터 적용을 수행하지 않았다.
실제 배포 후 허용된 연결, 허용되지 않은 연결, ALB 대상 상태 및 모니터링 수집을 확인해야 한다.

- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Helm 하위 차트의 values 범위](https://helm.sh/docs/chart_template_guide/subcharts_and_globals/)
- [EKS NetworkPolicy 설정](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html)
- [EKS NetworkPolicy 제약](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy.html)
- [Argo CD Sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)
