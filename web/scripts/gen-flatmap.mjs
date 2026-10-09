// 一次性数据生成脚本：把 world-atlas 110m TopoJSON 采样成手机端 hero 用的 2D 点阵地图数据。
// 产物 web/lib/flatmap-dots.json 体积 ~40KB（gzip ~12KB），运行时零依赖、不再请求 world-geo.json。
// 调整点距后重跑：node scripts/gen-flatmap.mjs
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { geoContains } from "d3-geo";
import { feature } from "topojson-client";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const topo = JSON.parse(readFileSync(path.join(root, "public", "world-geo.json"), "utf8"));
const geo = feature(topo, topo.objects.countries);
const land = geo.features.filter((f) => String(f.id ?? "") !== "010"); // 南极洲不上图

// 纬度裁掉南极圈以南，经纬步长 2°：358px 宽下点距约 2px，密度接近 cobe 球面观感
const LON_MIN = -180,
  LON_MAX = 180,
  LAT_MIN = -60,
  LAT_MAX = 84,
  STEP = 2;

const dots = [];
for (let lat = LAT_MIN + STEP / 2; lat < LAT_MAX; lat += STEP) {
  for (let lon = LON_MIN + STEP / 2; lon < LON_MAX; lon += STEP) {
    if (geoContains({ type: "FeatureCollection", features: land }, [lon, lat])) {
      dots.push(Math.round(lon), Math.round(lat));
    }
  }
}

mkdirSync(path.join(root, "lib"), { recursive: true });
const out = { latMin: LAT_MIN, latMax: LAT_MAX, step: STEP, dots };
writeFileSync(path.join(root, "lib", "flatmap-dots.json"), JSON.stringify(out));
console.log(`dots: ${dots.length / 2}, bytes: ${JSON.stringify(out).length}`);
