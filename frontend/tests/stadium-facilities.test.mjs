import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { mkdtempSync, readFileSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import ts from 'typescript';

const dir = mkdtempSync(join(tmpdir(),'stadium-facilities-'));
const modules = ['nearby-places','stadium-facilities'];
for (const name of modules) writeFileSync(join(dir,`${name}.js`),ts.transpileModule(readFileSync(new URL(`../lib/${name}.ts`,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText);
after(()=>{for(const name of modules)unlinkSync(join(dir,`${name}.js`));rmdirSync(dir);});
const { parseStadiumFacilities, filterStadiumFacilities, facilityPlace } = createRequire(join(dir,'index.cjs'))('./stadium-facilities.js');
const source={stadium:'JAMSIL',pageUrl:'https://myseatcheck.com/store/',imageUrl:'https://myseatcheck.com/wp-content/uploads/map.webp',note:'photo'};
const pin={id:'j-1',recordId:'SC_FOOD_JAMSIL_001',lat:37.512,lng:127.072,label:'2F 1',quality:'diagram_approximate',uncertaintyM:30,checkedAt:'2026-09-30',source};
const row={id:pin.recordId,stadium:'JAMSIL',name:'치킨',kind:'food',affiliation:'stadium',scope:'internal',scopeLabel:'구장 내부',floor:'2층',zone:'1루',sourceUrl:source.pageUrl,sourceCheckedAt:'2026-09-10',pins:[pin],locationStatus:'approximate_pin'};
const payload={stadium:'JAMSIL',count:1,pinCount:1,records:[row],warning:'approximate',review:{note:'partial'}};

test('same brand is separated by record, side, floor and scope',()=>{
  const outside={...row,id:'SC_FOOD_JAMSIL_002',scope:'exterior',scopeLabel:'구장 외부 부속',floor:'외부',pins:[]};
  assert.deepEqual(filterStadiumFacilities([row,outside],'치킨','internal'),[row]);
  assert.deepEqual(filterStadiumFacilities([row,outside],'2층 1루'),[row]);
  assert.deepEqual(filterStadiumFacilities([row,outside],'','exterior'),[outside]);
});
test('parser accepts provenance but rejects wrong venue, duplicates and fake coordinates',()=>{
  assert.equal(parseStadiumFacilities(payload,'JAMSIL'),payload);
  assert.throws(()=>parseStadiumFacilities(payload,'SUWON'));
  assert.throws(()=>parseStadiumFacilities({...payload,count:2,records:[row,row]},'JAMSIL'));
  for(const change of [{lat:null},{lng:NaN},{uncertaintyM:0},{source:{...source,pageUrl:'javascript:alert(1)'}},{source:{...source,stadium:'SUWON'}}]) {
    assert.throws(()=>parseStadiumFacilities({...payload,records:[{...row,pins:[{...pin,...change}]}]},'JAMSIL'));
  }
});
test('unlocated facilities remain list-only; selected pin retains source metadata',()=>{
  const data={...payload,pinCount:0,records:[{...row,pins:[],locationStatus:'zone_only'}]};
  assert.equal(parseStadiumFacilities(data,'JAMSIL').pinCount,0);
  const place=facilityPlace(row,pin,{code:'JAMSIL',name:'잠실',lat:37.512,lng:127.072,address:'서울'});
  assert.equal(place.category,'구장 내부');
  assert.equal(place.stadiumFacility.uncertaintyM,30);
  assert.match(place.placeId,/^stadium-facility:SC_FOOD_JAMSIL_001:/);
  assert.throws(()=>facilityPlace(row,pin,{code:'SUWON'}));
});
test('integration keeps catalogue separate and leaves course policy untouched',()=>{
  const component=readFileSync(new URL('../components/nearby-route-planner.tsx',import.meta.url),'utf8');
  assert.match(component,/placeCollection === "stadium"/);
  assert.match(component,/selectedPlacePins\(pinPlaces, selected, stops\)/);
  assert.match(component,/useStadiumFacilities\(stadium.code, !drawOnly\)/);
  assert.match(component,/stadiumAffiliation\.label/);
  const backend=readFileSync(new URL('../../backend/travel/stadium_facilities.py',import.meta.url),'utf8');
  assert.doesNotMatch(backend,/courseEligible|exclude_from_course|eligible_for_course/);
});
