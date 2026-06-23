"""
Test Factories for Baldur Tests.

이 패키지는 테스트용 Mock 객체와 데이터를 생성하는 Factory 패턴을 제공합니다.

주요 구성요소:
- TestDataFactory: 테스트 데이터 생성 (FailedOperationData, CircuitBreakerStateData 등)
- MockRedisClient: 통합된 Redis Mock 클라이언트
- InMemoryCircuitBreakerRepository: CB 상태 저장용 인메모리 Repository
- InMemoryDLQRepository: DLQ 엔트리 저장용 인메모리 Repository
- Constants: 테스트 상수 (Domains, Services, FailureTypes, Status, CircuitState)

사용 예시:
    from tests.factories import TestDataFactory, MockRedisClient, Domains, Status
    from tests.factories.repositories import InMemoryCircuitBreakerRepository

    # 테스트 데이터 생성 (상수 사용)
    cb_state = TestDataFactory.circuit_breaker_state(service_name="payment-api")
    failed_op = TestDataFactory.failed_operation(domain=Domains.ORDER, status=Status.PENDING)

    # Mock Redis 사용
    redis = MockRedisClient()
    redis.set("key", "value")

    # Repository 사용
    repo = InMemoryCircuitBreakerRepository()
    state = repo.get_or_create("test_service")
"""

# Constants (constants.py)
# Builders (builders.py)
from tests.factories.builders import (
    CanaryRolloutBuilder,
    CanaryStageBuilder,
    ChaosExperimentBuilder,
    CircuitBreakerStateBuilder,
    FailedOperationBuilder,
    MockRequestBuilder,
    MockServiceBuilder,
    WatchdogConfigBuilder,
)
from tests.factories.constants import (
    CanaryCluster,
    CanaryPercentage,
    CeleryTestConfig,
    ChaosIntensity,
    CircuitState,
    DatabaseTestConfig,
    DefaultValues,
    Domains,
    FailureTypes,
    RBACRole,
    RedisTestConfig,
    Services,
    Status,
    TestConstants,
)
from tests.factories.data_factory import (
    MockCanaryRolloutData,
    MockCircuitBreakerStateData,
    MockFailedOperationData,
    TestDataFactory,
)
from tests.factories.execution_mode_helpers import dry_run_active

# Integration (integration.py)
from tests.factories.integration import (
    CeleryTaskRunner,
    IntegrationTestContext,
    RealDatabaseFactory,
    RealRedisClientFactory,
)
from tests.factories.redis import (
    FakeRawRedis,
    FakeRedisAdapter,
    MockDistributedLock,
    MockPipeline,
    MockRedisClient,
)
from tests.factories.repositories import (
    InMemoryCircuitBreakerRepository,
    InMemoryDLQRepository,
    InMemoryRateLimitTracker,
    MockDLQEntry,
)
from tests.factories.time_helpers import (
    MockSleep,
    freeze_time,
    get_fixed_datetime,
    make_datetime_range,
    mock_sleep,
)

__all__ = [
    # Constants
    "DefaultValues",
    "Domains",
    "Services",
    "FailureTypes",
    "Status",
    "CircuitState",
    "TestConstants",
    "CeleryTestConfig",
    "RedisTestConfig",
    "DatabaseTestConfig",
    "CanaryCluster",
    "CanaryPercentage",
    "ChaosIntensity",
    "RBACRole",
    # Data Factory
    "TestDataFactory",
    "MockCircuitBreakerStateData",
    "MockFailedOperationData",
    "MockCanaryRolloutData",
    # Redis
    "MockRedisClient",
    "MockPipeline",
    "MockDistributedLock",
    "FakeRawRedis",
    "FakeRedisAdapter",
    # Repositories
    "InMemoryCircuitBreakerRepository",
    "InMemoryRateLimitTracker",
    "InMemoryDLQRepository",
    "MockDLQEntry",
    # Execution Mode / Dry-run Helpers
    "dry_run_active",
    # Time Helpers
    "freeze_time",
    "mock_sleep",
    "MockSleep",
    "get_fixed_datetime",
    "make_datetime_range",
    # Builders
    "CircuitBreakerStateBuilder",
    "FailedOperationBuilder",
    "CanaryRolloutBuilder",
    "MockServiceBuilder",
    "MockRequestBuilder",
    "CanaryStageBuilder",
    "ChaosExperimentBuilder",
    "WatchdogConfigBuilder",
    # Integration
    "RealRedisClientFactory",
    "RealDatabaseFactory",
    "CeleryTaskRunner",
    "IntegrationTestContext",
]
