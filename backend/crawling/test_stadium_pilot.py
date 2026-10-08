import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_stadium_pilot as c


class PilotTests(unittest.TestCase):
    def test_radius_and_bad_coordinates(self):
        center = (37.5161987797456, 127.075940589715)
        self.assertIsNotNone(c.record('TEST','1','test','cafe','',*center,center,{}))
        self.assertIsNone(c.record('TEST','1','test','cafe','',38,127,center,{}))
        self.assertIsNone(c.record('TEST','1','test','cafe','','NaN',127,center,{}))

    def test_pagination_rejects_repeated_page(self):
        payload = {'header':{'resultCode':'00'},'body':{'items':[{'id':1}],'totalCount':2}}
        with patch.object(c,'get',return_value=payload), patch.object(c.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'repeated'):
                c.all_pages('https://example.invalid',{},size=1)

    def test_provider_failure_not_empty_success(self):
        with self.assertRaises(RuntimeError):
            c.page_items({'response':{'header':{'resultCode':'30'}}})

    def test_same_building_is_review_not_confirmed_match(self):
        doc = dict(id='1',place_name='다른가게',road_address_name='서울특별시 송파구 올림픽로 10',
                   y='37.5162',x='127.0759',place_url='https://place.map.kakao.com/1')
        source = dict(source_id='A',name='테스트식당',address='서울특별시 송파구 올림픽로 10',lat=37.5162,lng=127.0759)
        with patch.object(c,'get',return_value={'documents':[doc],'meta':{'is_end':True}}):
            result = c.compare_kakao('fixture',[source],(37.5162,127.0759))
        self.assertEqual(result['sample_size'],1)
        self.assertEqual(result['findings'][0]['status'],'manual_review')
        self.assertNotIn('place_name',result['findings'][0])

    def test_offline_convenience_scope_preserves_unresolved_and_originals(self):
        def row(ident, name, small='편의점', group='G2', kind='other'):
            return dict(source='SBIZ', source_id=ident, name=name, kind=kind,
                        source_fields={'indsLclsCd': group, 'indsSclsNm': small})

        rows = [row('cu', '씨유 잠실점'), row('gs', 'ＧＳ２５ 잠실점'),
                row('seven', '세븐 일레븐 잠실점'), row('emart', '이마트 24 잠실점'),
                row('corporate', '세븐잠실점 코리아'), row('legacy', '미니스톱 잠실점'),
                row('unknown', 'CUBE마트'), row('conflict', 'CU GS25 잠실점'),
                row('bakery', 'CU베이커리', '빵/도넛', 'I2', 'restaurant'),
                row('play', '테스트 보드게임카페', '카페', 'I2', 'cafe')]
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            raw = json.dumps(rows, ensure_ascii=False).encode('utf-8')
            (out/'sbiz.json').write_bytes(raw)
            summary = {'collected_at': 'fixture', 'center': {}, 'sources': {}}
            with patch.object(c, 'get', side_effect=AssertionError('Offline rebuild called API')):
                c.write_report(out, summary)
                first = {name: (out/name).read_bytes() for name in
                         ('places.json', 'convenience_stores.json', 'summary.json', 'REPORT.md')}
                c.write_report(out, summary)
            self.assertEqual((out/'sbiz.json').read_bytes(), raw)
            for name, data in first.items():
                self.assertEqual((out/name).read_bytes(), data)
            places = json.loads((out/'places.json').read_text(encoding='utf-8'))
            selected = {p['source_id']: p for p in places}
            self.assertEqual(set(selected), {'cu', 'gs', 'seven', 'emart', 'bakery', 'play'})
            for ident, brand in (('cu', 'CU'), ('gs', 'GS25'), ('seven', '세븐일레븐'), ('emart', '이마트24')):
                self.assertEqual(selected[ident]['kind'], 'convenience_store')
                self.assertEqual(selected[ident]['brand'], brand)
            self.assertEqual(selected['bakery']['kind'], 'restaurant')
            self.assertEqual(selected['play']['kind'], 'play_facility')
            audit = {p['source_id']: p for p in json.loads((out/'convenience_stores.json').read_text(encoding='utf-8'))}
            self.assertEqual(len(audit), 8)
            self.assertIsNone(audit['corporate']['brand'])
            self.assertEqual(audit['corporate']['brand_candidates'], ['세븐일레븐'])
            self.assertEqual(audit['legacy']['brand_status'], 'legacy_name')
            self.assertEqual(audit['unknown']['brand_status'], 'unidentified')
            self.assertEqual(audit['conflict']['brand_status'], 'needs_review')
            self.assertEqual(summary['places']['categories']['convenience_store'], 4)
            self.assertEqual(summary['convenience_stores']['included_records'], 4)


if __name__ == '__main__':
    unittest.main()
