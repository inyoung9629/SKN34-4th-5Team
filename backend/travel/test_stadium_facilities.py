import csv
import json
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from . import stadium_facilities as service
from .stadium_facility_views import StadiumFacilityListView
from .stadium_affiliation import stadium_affiliation_hint


class StadiumFacilityTests(SimpleTestCase):
    def test_public_candidates_need_both_venue_name_and_venue_address(self):
        row = {'source':'SBIZ','kind':'restaurant','name':'치킨 잠실야구장','address':'서울특별시 송파구 올림픽로 25'}
        hint = stadium_affiliation_hint('JAMSIL',row)
        self.assertEqual(hint['scope'],'unknown')
        self.assertEqual(hint['status'],'candidate')
        self.assertEqual(hint['coordinateStatus'],'shop_position_unverified')
        self.assertIsNone(stadium_affiliation_hint('JAMSIL',{**row,'name':'주경기장 치킨'}))
        self.assertIsNone(stadium_affiliation_hint('JAMSIL',{**row,'address':'서울 올림픽로 25-1'}))
        self.assertIsNone(stadium_affiliation_hint('JAMSIL',{**row,'source':'TOUR'}))
        self.assertIsNone(stadium_affiliation_hint('DAEJEON',{**row,'name':'대전야구장3루점','address':'대전 대종로 373'}))

    def test_removed_sbiz_candidates_are_absent_from_live_catalogue(self):
        from .collected_places import catalogue
        rows = catalogue('JAMSIL')['places']
        self.assertTrue(rows)
        self.assertTrue(all(row['source'] in {'PARK', 'TOUR'} for row in rows))
        self.assertTrue(all(row['stadiumAffiliation'] is None for row in rows))

    def test_all_census_records_include_source_exterior_food(self):
        codes = ('JAMSIL','GOCHEOK','MUNHAK','SUWON','DAEJEON','DAEGU','GWANGJU','SAJIK','CHANGWON')
        rows = [r for code in codes for r in service.facility_catalogue(code)['records']]
        self.assertEqual(len(rows), 488)
        self.assertEqual(len({r['id'] for r in rows}), 488)
        foods = [r for r in rows if r['kind'] == 'food']
        self.assertEqual(len(foods), 388)
        self.assertEqual(sum(r['scope'] == 'internal' for r in foods), 388)
        self.assertEqual(sum(r['scope'] == 'exterior' for r in foods), 0)
        self.assertTrue(all(r['affiliation'] == 'stadium' for r in rows))
        self.assertTrue(all(r['operatingStatus'] == 'unverified' for r in rows))
        self.assertTrue(all('courseEligible' not in r for r in rows))

    def test_food_has_reference_pins_and_unlocated_facilities_stay_list_only(self):
        rows = service.facility_catalogue('GOCHEOK')['records']
        self.assertTrue(rows)
        self.assertTrue(all(r['pin'] is None and r['pins'] == [] and r['locationStatus'] == 'zone_only' for r in rows if r['kind'] == 'facility'))
        self.assertTrue(all(r['pin']['quality'] == 'display_reference' and r['locationStatus'] == 'reference_pin' for r in rows if r['kind'] == 'food'))

    def test_outdoors_does_not_imply_outside_ticket_gates(self):
        self.assertEqual(service._scope({'indoor_outdoor':'실외','floor':'1층','location_detail':'외야'},'facility'), 'unknown')
        self.assertEqual(service._scope({'floor':'외부','location_detail':'1루'},'facility'), 'exterior')

    def test_pin_provenance_same_brand_branches_and_floors(self):
        data = service.facility_catalogue('JAMSIL')
        pins = [p for r in data['records'] for p in r['pins']]
        self.assertEqual(data['pinCount'], len(pins))
        bhc = [r for r in data['records'] if r['name'] == 'BHC치킨']
        self.assertGreaterEqual(len(bhc), 3)
        self.assertGreaterEqual(len({r['zone'] for r in bhc}), 3)
        for p in pins:
            if p['recordId'].startswith('SC_FOOD_'):
                self.assertEqual(p['quality'], 'display_reference')
                self.assertEqual(p['uncertaintyM'], 0)
                self.assertIn('실제 매장 위치', p['source']['note'])
                continue
            self.assertEqual(p['quality'], 'diagram_approximate')
            self.assertEqual(p['source']['stadium'], 'JAMSIL')
            self.assertIn('/wp-content/uploads/', p['source']['imageUrl'])
            self.assertGreaterEqual(p['uncertaintyM'], 10)

    def test_reviewed_internal_pins_do_not_escape_actual_outer_rings(self):
        boundary = json.loads((service._root() / 'stadium_boundaries.json').read_text(encoding='utf-8'))
        def inside(pin, ring):
            flag = False
            for first, second in zip(ring, ring[1:] + ring[:1]):
                y, x = first
                by, bx = second
                if (y > pin['lat']) != (by > pin['lat']) and pin['lng'] < (bx-x)*(pin['lat']-y)/(by-y)+x:
                    flag = not flag
            return flag
        count = 0
        for code in boundary['stadiums']:
            for row in service.facility_catalogue(code)['records']:
                for pin in row['pins']:
                    count += 1
                    if row['scope'] == 'internal' and pin['quality'] != 'display_reference':
                        self.assertTrue(any(inside(pin, ring) for ring in boundary['stadiums'][code]['rings']), pin['id'])
        self.assertEqual(count, 388)

    def test_bad_queries_and_missing_data_fail_explicitly(self):
        view = StadiumFacilityListView.as_view()
        for query in ({},{'stadium':'../JAMSIL'},{'stadium':'UNKNOWN'},{'stadium':['JAMSIL','SUWON']},{'stadium':'JAMSIL','extra':'x'}):
            self.assertEqual(view(APIRequestFactory().get('/', query)).status_code, 400)
        with patch.object(service, '_root', return_value=Path('/missing-facility-test-data')):
            self.assertEqual(view(APIRequestFactory().get('/',{'stadium':'JAMSIL'})).status_code,503)

    def test_source_csv_contains_detail_links_without_claiming_coordinates(self):
        with (service._root() / service.FILES[0]).open(encoding='utf-8-sig',newline='') as stream:
            reader = csv.DictReader(stream)
            self.assertNotIn('lat',reader.fieldnames)
            rows = list(reader)
            self.assertEqual(len(rows), 388)
            self.assertTrue(all(r['source_location'] and r['source_title'] for r in rows))
            self.assertTrue(all(r['source_url'] != r['listing_url'] for r in rows))
            self.assertEqual(sum(r['food_category'] == 'CAFE' for r in rows), 71)

    def test_every_food_pin_is_offset_and_links_to_the_observed_store_location(self):
        import math
        centers = json.loads((service._root() / 'stadium_locations.json').read_text(encoding='utf-8'))['stadiums']
        for code, center in centers.items():
            foods = [r for r in service.facility_catalogue(code)['records'] if r['kind'] == 'food']
            self.assertTrue(foods)
            for row in foods:
                pin = row['pin']
                dy = (pin['lat'] - center['lat']) * 111320
                dx = (pin['lng'] - center['lng']) * 111320 * math.cos(math.radians(center['lat']))
                self.assertTrue(54 < math.hypot(dx, dy) < 66, row['id'])
                self.assertEqual(pin['source']['pageUrl'], row['sourceUrl'])
                from .stadium_scope import classify_stadium_point
                self.assertEqual(classify_stadium_point(pin), {'scope': 'internal', 'stadium': code})
                self.assertIn(row['foodCategory'], ['FOOD', 'CAFE', 'CONVENIENCE'])
        rows = service.facility_catalogue('JAMSIL')['records']
        cafe = next(r for r in rows if r['name'] == '카페바른생활')
        self.assertEqual(cafe['sourceScope'], 'exterior')
        self.assertEqual(cafe['scope'], 'internal')
        self.assertEqual(cafe['sourceLocation'], '외부 3루 방면')
        self.assertIn('카페바른생', cafe['sourceUrl'])
        fries = next(r for r in rows if r['name'] == '브뤼셀프라이')
        self.assertEqual(fries['sourceLocation'], '1루 2.5층')
        self.assertIn('브뤼셀프라이', fries['sourceUrl'])
