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

    def test_public_candidate_annotation_does_not_change_record_or_coordinates(self):
        from .collected_places import catalogue
        row = next(p for p in catalogue('JAMSIL')['places'] if p['placeId']=='collected:SBIZ:MA010120220804279123')
        self.assertEqual(row['stadiumAffiliation']['scope'],'unknown')
        self.assertEqual(row['kind'],'cafe')
        self.assertAlmostEqual(row['lat'],37.5161925601351)
        self.assertNotIn('courseEligible',row)

    def test_all_source_records_are_separate_and_original_counts_preserved(self):
        codes = ('JAMSIL','GOCHEOK','MUNHAK','SUWON','DAEJEON','DAEGU','GWANGJU','SAJIK','CHANGWON')
        rows = [r for code in codes for r in service.facility_catalogue(code)['records']]
        self.assertEqual(len(rows), 485)
        self.assertEqual(len({r['id'] for r in rows}), 485)
        foods = [r for r in rows if r['kind'] == 'food']
        self.assertEqual(len(foods), 385)
        self.assertEqual(sum(r['scope'] == 'internal' for r in foods), 348)
        self.assertEqual(sum(r['scope'] == 'exterior' for r in foods), 37)
        self.assertTrue(all(r['affiliation'] == 'stadium' for r in rows))
        self.assertTrue(all(r['operatingStatus'] == 'unverified' for r in rows))
        self.assertTrue(all('courseEligible' not in r for r in rows))

    def test_unknown_location_has_no_centroid_fallback(self):
        rows = service.facility_catalogue('GOCHEOK')['records']
        self.assertTrue(rows)
        self.assertTrue(all(r['pin'] is None and r['pins'] == [] and r['locationStatus'] == 'zone_only' for r in rows))

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
                    if row['scope'] == 'internal':
                        self.assertTrue(any(inside(pin, ring) for ring in boundary['stadiums'][code]['rings']), pin['id'])
        self.assertEqual(count, 74)

    def test_bad_queries_and_missing_data_fail_explicitly(self):
        view = StadiumFacilityListView.as_view()
        for query in ({},{'stadium':'../JAMSIL'},{'stadium':'UNKNOWN'},{'stadium':['JAMSIL','SUWON']},{'stadium':'JAMSIL','extra':'x'}):
            self.assertEqual(view(APIRequestFactory().get('/', query)).status_code, 400)
        with patch.object(service, '_root', return_value=Path('/missing-facility-test-data')):
            self.assertEqual(view(APIRequestFactory().get('/',{'stadium':'JAMSIL'})).status_code,503)

    def test_original_source_csv_is_read_only_and_has_no_new_coordinate_columns(self):
        with (service._root() / service.FILES[0]).open(encoding='utf-8-sig',newline='') as stream:
            reader = csv.DictReader(stream)
            self.assertNotIn('lat',reader.fieldnames)
            self.assertEqual(len(list(reader)),385)
