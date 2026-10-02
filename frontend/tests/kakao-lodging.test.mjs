import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const scratch = mkdtempSync(join(tmpdir(), "kbo-lodging-test-"));
after(() => rmSync(scratch, { recursive: true }));
for (const name of ["kakao-lodging", "google-lodging", "nearby-places", "stadiums", "stadium-locations", "stadium-boundaries", "stadium-boundary-frame"]) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText);
}
const require = createRequire(join(scratch, "entry.cjs"));
const { searchKakaoLodging, lodgingSubtype, kakaoLodgingPlace, refreshKakaoLodgingStops } = require("./kakao-lodging.js");
const { referenceOnlyStop, canRequestDirections, kakaoLodgingReference } = require("./google-lodging.js");
const { sameStop } = require("./nearby-places.js");
const { stadiums } = require("./stadiums.js");
const stadium = stadiums.find(item => item.code === "JAMSIL");
const item = changes => ({id:"123",place_name:"테스트 호텔",category_group_code:"AD5",category_name:"숙박 > 호텔",x:"127.080",y:"37.510",road_address_name:"서울 테스트로 1",address_name:"서울",...changes});
const payload = (places, next=false) => ({ok:true,json:async()=>({places,hasNextPage:next})});

test("shared-list lodging retains reference-only storage and restores old visit identity", () => {
  const { normalizePlace } = require("./nearby-places.js");
  const place = kakaoLodgingPlace(normalizePlace(item({ category_name: "여행 > 숙박 > 여관,모텔" }), stadium));
  assert.equal(place.placeId, "kakao-lodging:all:123");
  assert.equal(place.subcategory, "모텔·여관(통합 분류)");
  assert.equal(referenceOnlyStop(place).name, "선택한 숙소");
  assert.equal(referenceOnlyStop(place).address, undefined);
  assert.ok(Number.isNaN(referenceOnlyStop(place).lat));
  const old = { ...referenceOnlyStop(place), placeId: "kakao-lodging:hotel:123", visitId: "saved-visit" };
  const other = { name: "식당", placeId: "999", lat: 37.51, lng: 127.08, category: "먹거리" };
  const restored = refreshKakaoLodgingStops([old, other, {...old, visitId:"second-visit"}], place);
  assert.equal(restored[0].placeId, old.placeId);
  assert.equal(restored[0].visitId, old.visitId);
  assert.equal(restored[0].lat, place.lat);
  assert.equal(restored[0].name, place.name);
  assert.equal(restored[1], other);
  assert.equal(restored[2].visitId, "second-visit");
  assert.equal(canRequestDirections(restored), false);
});

test("live lookup uses corrected venue, AD5, bounded pages, no-store and no background pins", async () => {
  const requests=[];
  const result = await searchKakaoLodging(stadium,"all",new AbortController().signal,async (url, options) => { requests.push(JSON.parse(options.body)); assert.equal(url,"/api/v1/places/lodging/"); assert.equal(options.cache,"no-store"); return payload([item({})],true); });
  assert.equal(requests.length,3);
  assert.deepEqual(requests.map(q=>q.page),[1,2,3]);
  assert.ok(requests.every(q=>q.category==="AD5" && q.radius===2500 && q.size===15 && q.lat===stadium.lat));
  assert.equal(result.places.length,1);
  assert.equal(result.places[0].subcategory,"호텔");
});
test("provider category, not name or search query, controls hotel/motel classification", async () => {
  assert.equal(lodgingSubtype("숙박 > 호텔"),"호텔");
  assert.equal(lodgingSubtype("숙박"),"분류 확인 필요");
  assert.equal(lodgingSubtype("숙박 > 호텔,모텔"),"분류 확인 필요");
  assert.equal(lodgingSubtype("여행 > 숙박 > 여관,모텔"),"모텔·여관(통합 분류)");
  const result = await searchKakaoLodging(stadium,"hotel",new AbortController().signal,async()=>payload([item({place_name:"호텔 이름",category_name:"숙박 > 모텔"})]));
  assert.equal(result.places.length,0);
  const queries=[];
  await searchKakaoLodging(stadium,"inn",new AbortController().signal,async(_url,options)=>{queries.push(JSON.parse(options.body).keyword);return payload([]);});
  assert.deepEqual(queries,["여관","여인숙"]);
});
test("failure is explicit; abort ignores result; reference-only persistence strips content", async () => {
  await assert.rejects(searchKakaoLodging(stadium,"all",new AbortController().signal,async()=>({ok:false,json:async()=>({error:"연결 실패"})})),/연결 실패/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(searchKakaoLodging(stadium,"all",controller.signal));
  const stop={placeId:"kakao-lodging:hotel:123",name:"제공자 이름",category:"호텔",address:"제공자 주소",lat:37.51,lng:127.08};
  assert.ok(Number.isNaN(referenceOnlyStop(stop).lat));
  assert.equal(referenceOnlyStop(stop).address,undefined);
  assert.equal(referenceOnlyStop(stop).name,"선택한 숙소");
  assert.equal(canRequestDirections([stop]),false);
  assert.deepEqual(kakaoLodgingReference(stop),{kind:"hotel",id:"123"});
  assert.equal(sameStop(stop,{...stop,placeId:"kakao-lodging:all:123"}),true);
});
test("all nine current pins are inside their reviewed venue bounds; source generation stays synced", () => {
  const audit = require("./stadium-locations.js").stadiumLocationAudit;
  const sourceUrl = new URL("../../data/preprocessed/stadium_locations.json", import.meta.url);
  if(existsSync(sourceUrl)) assert.deepEqual(audit,JSON.parse(readFileSync(sourceUrl,"utf8")));
  assert.equal(stadiums.length,9);
  for(const stadium of stadiums){const p=audit.stadiums[stadium.code];assert.equal(stadium.lat,p.lat);assert.ok(stadium.lat>p.south&&stadium.lat<p.north&&stadium.lng>p.west&&stadium.lng<p.east);}
  assert.ok(stadiums.find(s=>s.code==="DAEJEON").lng>127.4303);
  assert.ok(stadiums.find(s=>s.code==="CHANGWON").lat>35.2216);
  const suwon = audit.stadiums.SUWON, daejeon = audit.stadiums.DAEJEON;
  assert.equal(suwon.osmType,"relation");
  assert.equal(suwon.osmId,7031781); // outer stadium, NOT the inner playing-field ring
  assert.ok(suwon.south<=37.2990142&&suwon.north>=37.3007398&&suwon.west<=127.0087544&&suwon.east>=127.0106755);
  assert.ok(daejeon.east>=127.4325024); // outfield extends beyond the building-only envelope
  assert.ok(daejeon.east>=127.432848&&daejeon.south<=36.3152833); // separate outfield grandstand building
  assert.ok(daejeon.excluded[0].lat>daejeon.north);
  assert.equal(audit.stadiums.GWANGJU.boundarySources.length,7); // horseshoe + field + bleachers + annexes + museum
});

test("inspection frames contain every reviewed facility/field vertex but exclude adjacent venues", () => {
  const audit=require("./stadium-locations.js").stadiumLocationAudit;
  const geometry=require("./stadium-boundaries.js").stadiumBoundaries;
  const {stadiumBoundaryFrame}=require("./stadium-boundary-frame.js");
  const contains=(path,[lat,lng])=>{
    let positive=false,negative=false;
    for(let i=0;i<path.length-1;i++){const a=path[i],b=path[i+1];const cross=(b[1]-a[1])*(lat-a[0])-(b[0]-a[0])*(lng-a[1]);if(cross>1e-12)positive=true;if(cross< -1e-12)negative=true;}
    return !(positive&&negative);
  };
  for(const [code,p] of Object.entries(audit.stadiums)){
    const rings=geometry.stadiums[code].rings,frame=stadiumBoundaryFrame(p,rings,audit.boundaryPaddingM);
    assert.ok(rings.flat().every(vertex=>contains(frame.path,vertex)),`${code}: all buildings and field vertices included`);
    assert.ok(p.excluded.every(place=>!contains(frame.path,[place.lat,place.lng])),`${code}: other venues excluded`);
    assert.ok(contains(frame.path,[p.lat,p.lng]),`${code}: first-team pin remains inside`);
  }
  const daejeon=stadiumBoundaryFrame(audit.stadiums.DAEJEON,geometry.stadiums.DAEJEON.rings,audit.boundaryPaddingM);
  assert.equal(daejeon.shape,"trimmed");
  assert.ok(contains(daejeon.path,[36.316,127.4323]));
  assert.ok(contains(daejeon.path,[36.3153,127.4326]),"Daejeon: outfield grandstand cannot be clipped");
  const gwangju=stadiumBoundaryFrame(audit.stadiums.GWANGJU,geometry.stadiums.GWANGJU.rings,audit.boundaryPaddingM);
  for(const vertex of [[35.16868,126.8897],[35.16845,126.8901],[35.16935,126.88956]]){
    assert.ok(contains(gwangju.path,vertex),"Gwangju: outfield, east annex and museum cannot be clipped by the horseshoe chord");
  }
  assert.ok(!contains(gwangju.path,[35.16888715,126.89069925]),"Gwangju: independent fire station excluded");
  const geometrySource=new URL("../../data/preprocessed/stadium_boundaries.json",import.meta.url);
  if(existsSync(geometrySource)) assert.deepEqual(geometry,JSON.parse(readFileSync(geometrySource,"utf8")));
});
