# HTTP 배포 호환성

브라우저의 회원·관리자·채팅 인증 요청은 Nginx의 `/api/v1/`를 통해 Django로 전달되며 `Authorization: Bearer <access token>`을 사용합니다. 인증 쿠키 설정은 필요하지 않습니다.

원격 HTTP에서는 브라우저 정책상 GPS, Web Share, 비동기 Clipboard API가 제한될 수 있습니다. 코스 공유는 선택 복사를 거쳐 실패 시 직접 복사할 내용을 표시하고, 로컬 UI 식별자는 `crypto.getRandomValues()` 또는 비보안 식별자 fallback을 사용합니다.
