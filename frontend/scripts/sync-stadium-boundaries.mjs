// Offline generated frontend copy; refresh public geometry with the PS1 fetcher.
import {readFileSync,writeFileSync} from "node:fs";
const data=JSON.parse(readFileSync(new URL("../../data/preprocessed/stadium_boundaries.json",import.meta.url),"utf8"));
const output=`// Generated from data/preprocessed/stadium_boundaries.json; OSM outer rings.\nexport const stadiumBoundaries = ${JSON.stringify(data,null,2)} as const;\n`;
const target=new URL("../lib/stadium-boundaries.ts",import.meta.url);
if(process.argv.includes("--check")){if(readFileSync(target,"utf8")!==output)throw new Error("구장 경계 생성본이 원본과 다릅니다.");}
else writeFileSync(target,output);
