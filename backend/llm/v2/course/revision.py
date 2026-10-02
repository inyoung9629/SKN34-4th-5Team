"""Room-owned previous route references, never model-generated place IDs."""
from .itinerary_request import ItineraryRequest


class RevisionClarification(ValueError):
    pass


def prepare_revision(request, previous, anchor):
    indices = request.replace_stop_indices
    if indices is None:
        return {}, set()
    previous = previous or {}
    game, result = previous.get('selected_game') or {}, previous.get('itinerary_result') or {}
    if (result.get('status') != 'ok' or not game.get('id') or
            any(game.get(k) != anchor.get(k) for k in ('id','stadium_code','starts_at'))):
        raise RevisionClarification('같은 경기에서 완성된 이전 코스가 있어야 일부 장소만 교체할 수 있어요. 전체 코스를 다시 만들까요?')
    old = ItineraryRequest.model_validate(previous['itinerary_request'])
    places = [s for s in result['stops'] if s['phase'] != 'game']
    if (len(old.stops) != len(request.stops) or len(places) != len(old.stops) or
            any(i >= len(old.stops) for i in indices) or any(s.phase == 'inside' for s in old.stops)):
        raise RevisionClarification('방문 개수·순서나 내부 시설까지 바뀌는 수정은 교체 대상을 다시 확인해야 해요. 전체 재작성을 원하시나요?')
    pinned, excluded = {}, set()
    for i, (before, after, place) in enumerate(zip(old.stops, request.stops, places)):
        if (before.kind, before.phase) != (after.kind, after.phase):
            raise RevisionClarification('부분 교체에서는 방문 종류·순서를 유지해야 해요. 바꿀 방문을 알려 주세요.')
        if i in indices:
            excluded.add(place['placeId'])
        else:
            before_fields = before.model_dump(exclude={'stay_minutes', 'required_keywords'})
            after_fields = after.model_dump(exclude={'stay_minutes', 'required_keywords'})
            old_names = set(before.required_keywords)
            new_names = set(after.required_keywords)
            added_names = new_names - old_names
            exact_name = lambda value: ''.join(value.casefold().split())
            only_reconfirmed_name = (old_names <= new_names and all(
                exact_name(v) == exact_name(place.get('name', '')) for v in added_names))
            if before_fields != after_fields or not only_reconfirmed_name:
                raise RevisionClarification('유지할 장소의 조건까지 변경되어 교체 범위를 확인해야 해요. 어떤 장소를 다시 고를까요?')
            if (place.get('kind') != after.kind or place.get('phase') != after.phase or
                    not place.get('placeId') or place.get('lat') is None or place.get('lng') is None):
                raise RevisionClarification('이전 장소의 위치를 확인하지 못했어요. 전체 코스를 다시 만들까요?')
            pinned[i] = dict(place)
    return pinned, excluded
