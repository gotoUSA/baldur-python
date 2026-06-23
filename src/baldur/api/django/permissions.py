"""
RBAC Permission Classes for Baldur Control API.

Provides role-based access control for the Baldur system:
- Viewer: Read-only access (dashboard, status, audit logs)
- Operator: Operational tasks (DLQ replay, archive)
- Admin: Full access (CB control, system enable/disable, config changes)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog
from rest_framework.permissions import BasePermission

from baldur.interfaces.web_framework import PermissionLevel

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = structlog.get_logger()


def _is_auth_disabled() -> bool:
    """Disable Baldur authentication in non-production environments only.

    Fail-Secure policy:
    - Production deploys can never bypass auth, even with
      ``DISABLE_BALDUR_AUTH=true``.
    - Production is detected via :func:`baldur.runtime.is_production` —
      the single canonical signal (``BALDUR_ENVIRONMENT == "production"``,
      strict equality). Legacy aliases (``prod``/``live``/``release``/
      ``stable``) and the ``DJANGO_SETTINGS_MODULE`` substring fallback
      are no longer honored; D15 hard-fails the known aliases at startup.
    - Production bypass attempts emit an ERROR log.
    """
    from baldur.runtime import is_production

    if is_production():
        if os.environ.get("DISABLE_BALDUR_AUTH", "").lower() in (
            "true",
            "1",
            "yes",
        ):
            logger.error("security.set_production_environment_auth")
        return False

    return os.environ.get("DISABLE_BALDUR_AUTH", "").lower() in (
        "true",
        "1",
        "yes",
    )


class IsBaldurAuthenticated(BasePermission):
    """
    인증된 사용자만 접근 허용 (테스트 환경 바이패스 지원).

    DISABLE_BALDUR_AUTH=true 환경 변수가 설정되면
    인증 없이도 접근을 허용합니다.
    """

    message = "Authentication required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True

        return bool(request.user and request.user.is_authenticated)


class IsViewer(BasePermission):
    """
    읽기 전용 권한 (Viewer 역할).

    허용되는 작업:
    - GET /status, GET /dashboard
    - GET /audit (감사 로그 조회)
    - GET /dlq/list, GET /dlq/<pk> (DLQ 조회)
    - GET /system/status (시스템 상태 조회)

    조건:
    - 인증된 사용자
    - staff 또는 'baldur_viewer' 그룹 멤버
    """

    message = "Baldur viewer permission required. Must be a member of the baldur_viewer group."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True

        if not request.user or not request.user.is_authenticated:
            return False

        # Admin/Staff는 항상 허용
        if request.user.is_staff:
            return True

        # baldur_viewer, operator, admin 그룹 멤버십 확인
        # (상위 권한은 하위 권한 포함)
        return bool(
            request.user.groups.filter(
                name__in=["baldur_viewer", "baldur_operator", "baldur_admin"]
            ).exists()
        )


class IsOperator(BasePermission):
    """
    운영자 권한 (Operator 역할).

    허용되는 작업:
    - 모든 Viewer 권한
    - POST /dlq/replay (DLQ 리플레이)
    - POST /dlq/cleanup/archive (DLQ 아카이브)
    - POST /dlq/<pk>/retry (개별 항목 재시도)
    - POST /dlq/<pk>/resolve (개별 항목 해결)

    조건:
    - 인증된 사용자
    - superuser 또는 'baldur_operator' 또는 'baldur_admin' 그룹 멤버
    """

    message = "Baldur operator permission required. Must be a member of the baldur_operator group."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True

        if not request.user or not request.user.is_authenticated:
            return False

        # Admin은 항상 허용
        if request.user.is_staff and request.user.is_superuser:
            return True

        # baldur_operator 또는 baldur_admin 그룹 멤버십 확인
        return bool(
            request.user.groups.filter(
                name__in=["baldur_operator", "baldur_admin"]
            ).exists()
        )


class IsBaldurAdmin(BasePermission):
    """
    관리자 권한 (Admin 역할).

    허용되는 작업:
    - 모든 Operator 권한
    - POST /control/ (CB 수동 제어: allow/block)
    - POST /system/enable, /system/disable (킬 스위치)
    - PUT /config/* (설정 변경)
    - POST /dlq/cleanup/purge (DLQ 영구 삭제)
    - Chaos Engineering 설정 변경

    조건:
    - 인증된 사용자
    - Django superuser 또는 'baldur_admin' 그룹 멤버

    보안:
    - Fail-Secure: 권한 확인 실패 시 거부
    """

    message = (
        "Baldur admin permission required. Must be a member of the baldur_admin group."
    )

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부

        Note:
            Fail-Secure: 예외 발생 시 거부
        """
        try:
            # 테스트 환경에서 인증 바이패스
            if _is_auth_disabled():
                return True

            if not request.user or not request.user.is_authenticated:
                return False

            # Django superuser
            if request.user.is_superuser:
                return True

            # baldur_admin 그룹
            return bool(request.user.groups.filter(name="baldur_admin").exists())

        except Exception as e:
            # Fail-Secure: 오류 시 거부
            logger.warning(
                "rbac.permission_check_failed_deny",
                error=e,
            )
            return False


class HasChaosTestPermission(BasePermission):
    """
    X-Test/Chaos 실험 API 권한 (2중 보안 장치 - 1차 Django RBAC).

    X-Test-Mode API에 대한 Django RBAC 기반 권한 클래스.
    헤더 검증(XTestModeMixin.check_chaos_permission)과 함께 2중 보안을 구성합니다.

    허용 조건 (OR):
    - 테스트 바이패스: DISABLE_BALDUR_AUTH=true
    - Django superuser
    - baldur_admin 그룹 멤버
    - baldur_chaos_tester 그룹 멤버

    차단 조건 (무조건):
    - ENVIRONMENT == production (Fail-Secure)

    로깅:
    - 권한 거부: WARNING (사용자, 이유)
    - 프로덕션 차단: ERROR
    - 권한 허용: DEBUG

    보안:
    - Fail-Secure: 모든 예외는 거부로 처리
    """

    message = "X-Test/Chaos experiment permission denied. Must be a member of baldur_admin or baldur_chaos_tester group."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        X-Test/Chaos API 접근 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부

        Note:
            Fail-Secure: 모든 예외 발생 시 거부
        """
        try:
            # 1. 테스트 환경 바이패스 (DISABLE_BALDUR_AUTH=true)
            if _is_auth_disabled():
                logger.debug(
                    "rbac.test_permission_bypassed_auth",
                    getattr=getattr(request, "path", "unknown"),
                )
                return True

            # 2. Production environments are unconditionally blocked (Fail-Secure).
            from baldur.runtime import is_production

            if is_production():
                logger.error(
                    "rbac.test_access_denied_production",
                    request_user=request.user,
                    getattr=getattr(request, "path", "unknown"),
                    client_ip=self._get_client_ip(request),
                )
                self.message = "X-Test/Chaos API is not available in production. Access blocked by security policy."
                return False

            # 3. 인증 필요
            if not request.user or not request.user.is_authenticated:
                logger.warning(
                    "rbac.test_permission_denied_authenticated",
                    getattr=getattr(request, "path", "unknown"),
                )
                self.message = "Authentication required to access X-Test/Chaos API."
                return False

            # 4. Django superuser 자동 허용
            if request.user.is_superuser:
                logger.debug(
                    "rbac.test_permission_granted_superuser",
                    request_user=request.user,
                )
                return True

            # 5. 그룹 기반 권한 체크 (baldur_admin 또는 baldur_chaos_tester)
            allowed_groups = ["baldur_admin", "baldur_chaos_tester"]
            if request.user.groups.filter(name__in=allowed_groups).exists():
                user_groups = list(
                    request.user.groups.filter(name__in=allowed_groups).values_list(
                        "name", flat=True
                    )
                )
                logger.debug(
                    "rbac.test_permission_granted_group",
                    request_user=request.user,
                    user_groups=user_groups,
                )
                return True

            # 6. 권한 없음 - 거부
            logger.warning(
                "rbac.test_permission_denied_no",
                request_user=request.user,
                allowed_groups=allowed_groups,
            )
            return False

        except Exception as e:
            # Fail-Secure: 예외 발생 시 거부
            logger.exception(
                "rbac.test_permission_check_failed",
                error=e,
                getattr=getattr(request, "user", "unknown"),
            )
            self.message = "Permission check failed. Access denied."
            return False

    def _get_client_ip(self, request: Request) -> str | None:
        """클라이언트 IP 추출."""
        from baldur.utils.network import extract_client_ip

        return extract_client_ip(request)


# =============================================================================
# PermissionLevel → DRF Permission mapping
# =============================================================================


def get_permission_instances(
    level: PermissionLevel,
) -> list[BasePermission]:
    """Convert a PermissionLevel enum to DRF permission class instances.

    Args:
        level: Framework-independent permission level

    Returns:
        List of DRF BasePermission instances for the given level
    """
    _PERMISSION_MAP: dict[PermissionLevel, list[type[BasePermission]]] = {
        PermissionLevel.PUBLIC: [],
        PermissionLevel.AUTHENTICATED: [IsBaldurAuthenticated],
        PermissionLevel.VIEWER: [IsViewer],
        PermissionLevel.OPERATOR: [IsOperator],
        PermissionLevel.ADMIN: [IsBaldurAdmin],
    }
    classes = _PERMISSION_MAP.get(level, [IsBaldurAuthenticated])
    return [cls() for cls in classes]


__all__ = [
    "IsBaldurAuthenticated",
    "IsViewer",
    "IsOperator",
    "IsBaldurAdmin",
    "HasChaosTestPermission",
    "get_permission_instances",
]
