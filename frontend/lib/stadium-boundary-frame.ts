type Coordinate = readonly [number, number]; // lat, lng
type Location = { lat: number; lng: number; south:number; north:number; west:number; east:number; excluded:readonly {lat:number;lng:number}[] };

// The smallest convex inspection frame containing all reviewed building/field
// vertices. Stadium interior courtyards/playing fields remain inside; no unrelated
// neighboring feature is used as input. Not a surveyed property boundary.
export function convexFrame(points: readonly Coordinate[]): Coordinate[] {
  const unique = new Map(points.map(point => [point.join(","), point]));
  const sorted = [...unique.values()].sort((a,b)=>a[1]-b[1]||a[0]-b[0]);
  const cross = (a:Coordinate,b:Coordinate,c:Coordinate) => (b[1]-a[1])*(c[0]-a[0])-(b[0]-a[0])*(c[1]-a[1]);
  const lower:Coordinate[]=[], upper:Coordinate[]=[];
  for(const p of sorted){while(lower.length>=2&&cross(lower.at(-2)!,lower.at(-1)!,p)<=0)lower.pop();lower.push(p);}
  for(const p of sorted.toReversed()){while(upper.length>=2&&cross(upper.at(-2)!,upper.at(-1)!,p)<=0)upper.pop();upper.push(p);}
  return [...lower.slice(0,-1),...upper.slice(0,-1)];
}

export function stadiumBoundaryFrame(point:Location, rings:readonly (readonly Coordinate[])[], paddingM=10): {shape:"rectangle"|"trimmed"; path:Coordinate[]; footprintRatio:number} {
  const latMargin=paddingM/111320,lngMargin=latMargin/Math.cos(point.lat*Math.PI/180);
  const vertices=rings.flat();
  const hull=convexFrame(vertices);
  const south=Math.min(point.south,...vertices.map(p=>p[0])),north=Math.max(point.north,...vertices.map(p=>p[0]));
  const west=Math.min(point.west,...vertices.map(p=>p[1])),east=Math.max(point.east,...vertices.map(p=>p[1]));
  // Translation keeps shoelace area numerically stable in geographic coordinates.
  const area=Math.abs(hull.reduce((sum,p,i)=>{const q=hull[(i+1)%hull.length];return sum+(p[1]-west)*(q[0]-south)-(q[1]-west)*(p[0]-south);},0))/2;
  const ratio=area/((north-south)*(east-west));
  const includesNeighbor=point.excluded.some(p=>p.lat>=south-latMargin&&p.lat<=north+latMargin&&p.lng>=west-lngMargin&&p.lng<=east+lngMargin);
  const rectangle:Coordinate[]=[[south-latMargin,west-lngMargin],[north+latMargin,west-lngMargin],[north+latMargin,east+lngMargin],[south-latMargin,east+lngMargin]];
  const shape=ratio>=.85&&!includesNeighbor?"rectangle":"trimmed";
  const path=shape==="rectangle"?rectangle:convexFrame(hull.flatMap(([lat,lng])=>[[lat-latMargin,lng-lngMargin],[lat-latMargin,lng+lngMargin],[lat+latMargin,lng-lngMargin],[lat+latMargin,lng+lngMargin]] as Coordinate[]));
  return {shape,path:[...path,path[0]],footprintRatio:ratio};
}
