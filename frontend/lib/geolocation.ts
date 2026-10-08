export type CurrentLocation = { lat: number; lng: number };

/** 권한 요청은 사용자가 위치 버튼을 눌렀을 때만 실행한다. */
export function requestCurrentLocation(
  onLocation: (point: CurrentLocation) => void,
  onError: (message: string) => void,
  environment = { secure: window.isSecureContext, geolocation: navigator.geolocation },
) {
  if (!environment.secure) {
    onError("현재 위치는 HTTPS 연결에서 사용할 수 있어요. 지도에서 출발지를 지정해 주세요.");
    return;
  }
  if (!environment.geolocation) {
    onError("이 브라우저는 위치 확인을 지원하지 않아요. 지도에서 출발지를 지정해 주세요.");
    return;
  }
  environment.geolocation.getCurrentPosition(({ coords }) => {
    const point = { lat: coords.latitude, lng: coords.longitude };
    if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng) || Math.abs(point.lat) > 90 || Math.abs(point.lng) > 180) {
      onError("위치 좌표를 확인하지 못했어요. 다시 시도하거나 지도에서 출발지를 지정해 주세요.");
      return;
    }
    onLocation(point);
  }, error => {
    onError(error.code === 1
      ? "위치 권한이 꺼져 있어요. 브라우저·기기의 위치 권한을 허용한 뒤 다시 누르거나 지도에서 출발지를 지정해 주세요."
      : error.code === 3
        ? "위치 확인 시간이 초과됐어요. 다시 시도하거나 지도에서 출발지를 지정해 주세요."
        : "현재 위치를 확인하지 못했어요. 기기의 위치 설정을 확인하거나 지도에서 출발지를 지정해 주세요.");
  }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
}
